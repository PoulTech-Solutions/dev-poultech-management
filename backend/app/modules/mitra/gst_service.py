"""GST settings, rate slabs, and sale line tax computation."""

from __future__ import annotations

import uuid
from decimal import Decimal

import structlog

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.db.models.mitra import MitraBillingSale, MitraGstRateSlab, MitraUnitBillingSettings
from app.modules.mitra.billing_schemas import BillingSaleCreateRequest
from app.modules.mitra.export_cache import drop_export_cache
from app.modules.mitra.gst import compute_gst_line
from app.modules.mitra.gst_schemas import (
    GstRateSlabCreate,
    GstRateSlabListResponse,
    GstRateSlabResponse,
    GstRateSlabUpdate,
    UnitBillingSettingsResponse,
    UnitBillingSettingsUpsert,
)

log = structlog.get_logger()


class MitraGstMixin:
    async def get_billing_settings(
        self, owner_user_id: uuid.UUID, unit_id: uuid.UUID
    ) -> UnitBillingSettingsResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        row = await self._repo.get_billing_settings(unit.id)
        if row is None:
            return UnitBillingSettingsResponse(
                unit_id=unit.id,
                is_gst_registered=False,
                seller_gstin=None,
                seller_legal_name=None,
                seller_state_code=None,
                default_place_of_supply_state=None,
                invoice_series_prefix=None,
                late_payment_interest_pct_annual=Decimal('0'),
                default_gst_rate_pct=None,
                created_at=unit.created_at,
                updated_at=unit.updated_at,
            )
        return UnitBillingSettingsResponse.model_validate(row)

    async def upsert_billing_settings(
        self, owner_user_id: uuid.UUID, unit_id: uuid.UUID, payload: UnitBillingSettingsUpsert
    ) -> UnitBillingSettingsResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        data = payload.model_dump()
        if data.get('is_gst_registered'):
            if not data.get('seller_gstin'):
                raise AppError(
                    'seller_gstin is required when GST registered',
                    code='mitra_gst_settings_invalid',
                )
            if not data.get('seller_state_code') and data.get('seller_gstin'):
                data['seller_state_code'] = data['seller_gstin'][:2]
        row = await self._repo.get_billing_settings(unit.id)
        if row is None:
            row = MitraUnitBillingSettings(unit_id=unit.id, owner_user_id=owner_user_id, **data)
            await self._repo.add_billing_settings(row)
        else:
            for key, value in data.items():
                setattr(row, key, value)
        await self._db.commit()
        await self._db.refresh(row)
        await drop_export_cache(owner_user_id, unit.id)
        log.info('mitra_billing_settings_updated', user_id=str(owner_user_id), unit_id=str(unit_id))
        return UnitBillingSettingsResponse.model_validate(row)

    async def list_gst_slabs(
        self, owner_user_id: uuid.UUID, *, limit: int, offset: int, active_only: bool
    ) -> GstRateSlabListResponse:
        rows, total = await self._repo.list_gst_slabs(
            owner_user_id, limit=limit, offset=offset, active_only=active_only
        )
        return GstRateSlabListResponse(
            items=[GstRateSlabResponse.model_validate(r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def create_gst_slab(
        self, owner_user_id: uuid.UUID, payload: GstRateSlabCreate
    ) -> GstRateSlabResponse:
        if await self._repo.get_gst_slab_by_hsn(owner_user_id, payload.hsn_sac):
            raise ConflictError(
                'HSN/SAC already exists in your rate list',
                code='mitra_gst_slab_duplicate',
            )
        row = MitraGstRateSlab(
            owner_user_id=owner_user_id,
            hsn_sac=payload.hsn_sac.strip(),
            label=payload.label,
            gst_rate_pct=payload.gst_rate_pct,
            effective_from=payload.effective_from,
            notes=payload.notes,
        )
        await self._repo.add_gst_slab(row)
        await self._db.commit()
        await self._db.refresh(row)
        log.info(
            'mitra_gst_slab_created',
            user_id=str(owner_user_id),
            slab_id=str(row.id),
            hsn_sac=row.hsn_sac,
        )
        return GstRateSlabResponse.model_validate(row)

    async def update_gst_slab(
        self, owner_user_id: uuid.UUID, slab_id: uuid.UUID, payload: GstRateSlabUpdate
    ) -> GstRateSlabResponse:
        row = await self._repo.get_gst_slab(owner_user_id, slab_id)
        if row is None:
            raise NotFoundError('GST rate slab not found', code='mitra_gst_slab_not_found')
        data = payload.model_dump(exclude_unset=True)
        if 'hsn_sac' in data:
            clash = await self._repo.get_gst_slab_by_hsn(owner_user_id, data['hsn_sac'])
            if clash is not None and clash.id != row.id:
                raise ConflictError(
                    'HSN/SAC already exists in your rate list',
                    code='mitra_gst_slab_duplicate',
                )
        for key, value in data.items():
            setattr(row, key, value)
        await self._db.commit()
        await self._db.refresh(row)
        log.info(
            'mitra_gst_slab_updated',
            user_id=str(owner_user_id),
            slab_id=str(slab_id),
        )
        return GstRateSlabResponse.model_validate(row)

    async def delete_gst_slab(self, owner_user_id: uuid.UUID, slab_id: uuid.UUID) -> None:
        row = await self._repo.get_gst_slab(owner_user_id, slab_id)
        if row is None:
            raise NotFoundError('GST rate slab not found', code='mitra_gst_slab_not_found')
        row.is_active = False
        await self._db.commit()
        log.info(
            'mitra_gst_slab_deactivated',
            user_id=str(owner_user_id),
            slab_id=str(slab_id),
        )

    async def _resolve_gst_rate(
        self, owner_user_id: uuid.UUID, hsn_sac: str | None, override_rate: Decimal | None, settings
    ) -> Decimal:
        if override_rate is not None:
            return override_rate
        if hsn_sac:
            slab = await self._repo.get_gst_slab_by_hsn(owner_user_id, hsn_sac)
            if slab is not None and slab.is_active:
                return slab.gst_rate_pct
        if settings and settings.default_gst_rate_pct is not None:
            return settings.default_gst_rate_pct
        return Decimal('0')

    async def _apply_gst_to_new_sale(
        self,
        owner_user_id: uuid.UUID,
        payload: BillingSaleCreateRequest,
        settings: MitraUnitBillingSettings | None,
    ) -> dict:
        line_subtotal = payload.quantity * payload.rate_inr
        if payload.amount_inr is not None and not (settings and settings.is_gst_registered):
            line_subtotal = payload.amount_inr

        pos = payload.place_of_supply_state
        pos = pos or (settings.default_place_of_supply_state if settings else None)

        gst_rate = await self._resolve_gst_rate(
            owner_user_id, payload.hsn_sac, payload.gst_rate_pct, settings
        )
        registered = bool(settings and settings.is_gst_registered)
        tax_inc = payload.tax_inclusive

        apply_tax = registered and gst_rate > 0
        if apply_tax and (not settings or not settings.seller_state_code or not pos):
            raise AppError(
                'Seller state and place of supply are required for GST invoices',
                code='mitra_gst_state_required',
            )

        totals = compute_gst_line(
            line_value_inr=line_subtotal,
            gst_rate_pct=gst_rate,
            seller_state_code=settings.seller_state_code if settings else None,
            place_of_supply_state=pos,
            tax_inclusive=tax_inc,
            is_gst_registered=apply_tax and gst_rate > 0,
        )

        invoice_kind = payload.invoice_kind or (
            'tax_invoice' if registered and totals.total_tax_inr > 0 else 'receipt'
        )
        if invoice_kind == 'bill_of_supply' and totals.total_tax_inr > 0:
            raise AppError(
                'Bill of supply cannot include GST tax; use tax_invoice or adjust rate',
                code='mitra_gst_invoice_kind_invalid',
            )
        grand = totals.grand_total_inr
        received = payload.received_inr if payload.received_inr is not None else grand
        balance = payload.balance_inr if payload.balance_inr is not None else grand - received

        return {
            'invoice_kind': invoice_kind,
            'hsn_sac': payload.hsn_sac,
            'buyer_gstin': payload.buyer_gstin,
            'place_of_supply_state': pos,
            'supply_type': totals.supply_type,
            'tax_inclusive': tax_inc,
            'taxable_value_inr': totals.taxable_value_inr,
            'gst_rate_pct': totals.gst_rate_pct,
            'cgst_inr': totals.cgst_inr,
            'sgst_inr': totals.sgst_inr,
            'igst_inr': totals.igst_inr,
            'total_tax_inr': totals.total_tax_inr,
            'amount_inr': grand,
            'received_inr': received.quantize(Decimal('0.01')),
            'balance_inr': balance.quantize(Decimal('0.01')),
        }

    async def _reapply_gst_on_sale_row(
        self, owner_user_id: uuid.UUID, row: MitraBillingSale, settings: MitraUnitBillingSettings | None
    ) -> None:
        if row.hsn_sac:
            gst_rate = await self._resolve_gst_rate(owner_user_id, row.hsn_sac, None, settings)
        else:
            gst_rate = await self._resolve_gst_rate(
                owner_user_id, None, row.gst_rate_pct if row.gst_rate_pct else None, settings
            )
        line_subtotal = row.quantity * row.rate_inr
        pos = row.place_of_supply_state or (
            settings.default_place_of_supply_state if settings else None
        )
        registered = bool(settings and settings.is_gst_registered)
        apply_tax = registered and gst_rate > 0
        if apply_tax and (not settings or not settings.seller_state_code or not pos):
            raise AppError(
                'Seller state and place of supply are required for GST invoices',
                code='mitra_gst_state_required',
            )
        totals = compute_gst_line(
            line_value_inr=line_subtotal,
            gst_rate_pct=gst_rate,
            seller_state_code=settings.seller_state_code if settings else None,
            place_of_supply_state=pos,
            tax_inclusive=row.tax_inclusive,
            is_gst_registered=apply_tax and gst_rate > 0,
        )
        row.taxable_value_inr = totals.taxable_value_inr
        row.gst_rate_pct = totals.gst_rate_pct
        row.cgst_inr = totals.cgst_inr
        row.sgst_inr = totals.sgst_inr
        row.igst_inr = totals.igst_inr
        row.total_tax_inr = totals.total_tax_inr
        row.supply_type = totals.supply_type
        row.amount_inr = totals.grand_total_inr
        if row.received_inr > row.amount_inr:
            row.received_inr = row.amount_inr
        row.balance_inr = (row.amount_inr - row.received_inr).quantize(Decimal('0.01'))
