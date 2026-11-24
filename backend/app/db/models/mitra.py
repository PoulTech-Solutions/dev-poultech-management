from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.users import User


class MitraFarmUnit(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = 'mitra_farm_units'

    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    farm_type: Mapped[str] = mapped_column(String(32), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    district: Mapped[str] = mapped_column(String(100), nullable=False)
    tehsil: Mapped[str | None] = mapped_column(String(100))
    village_city: Mapped[str | None] = mapped_column(String(100))
    pincode: Mapped[str | None] = mapped_column(String(6))
    address_line1: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text())
    hatch_date: Mapped[date | None] = mapped_column(Date())
    birds_placed: Mapped[int | None] = mapped_column()
    proprietor: Mapped[str | None] = mapped_column(String(200))

    owner: Mapped[User] = relationship(lazy='raise')

    __table_args__ = (
        CheckConstraint(
            "farm_type IN ("
            "'layer','broiler','pullet','breeder','hatchery',"
            "'kadaknath','duck_layer','duck_meat','quail','turkey','desi'"
            ")",
            name='ck_mitra_farm_units_type',
        ),
        CheckConstraint("contact_phone ~ '^[+]91[6-9][0-9]{9}$'", name='ck_mitra_farm_units_phone'),
        CheckConstraint("pincode IS NULL OR pincode ~ '^[1-9][0-9]{5}$'", name='ck_mitra_farm_units_pincode'),
        Index(
            'ix_mitra_farm_units_owner_active',
            'owner_user_id',
            postgresql_where=text('deleted_at IS NULL'),
        ),
    )


class MitraDailyRecord(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = 'mitra_daily_records'

    unit_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey('mitra_farm_units.id', ondelete='CASCADE'), nullable=False, index=True
    )
    record_date: Mapped[date] = mapped_column(Date(), nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)

    unit: Mapped[MitraFarmUnit] = relationship(lazy='raise')

    __table_args__ = (
        UniqueConstraint('unit_id', 'record_date', name='uq_mitra_daily_unit_date'),
        Index('ix_mitra_daily_records_unit_date', 'unit_id', 'record_date'),
    )


class MitraHatcheryBatch(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = 'mitra_hatchery_batches'

    unit_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey('mitra_farm_units.id', ondelete='CASCADE'), nullable=False, index=True
    )
    batch_code: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)

    unit: Mapped[MitraFarmUnit] = relationship(lazy='raise')

    __table_args__ = (UniqueConstraint('unit_id', 'batch_code', name='uq_mitra_hatchery_batch_code'),)


class MitraBillingSale(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = 'mitra_billing_sales'

    unit_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey('mitra_farm_units.id', ondelete='CASCADE'), nullable=False, index=True
    )
    invoice_no: Mapped[str] = mapped_column(String(64), nullable=False)
    sale_date: Mapped[date] = mapped_column(Date(), nullable=False)
    flock_batch_ref: Mapped[str | None] = mapped_column(String(64))
    buyer: Mapped[str] = mapped_column(String(200), nullable=False)
    item: Mapped[str] = mapped_column(String(120), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    qty_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    rate_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    amount_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    pay_mode: Mapped[str | None] = mapped_column(String(32))
    received_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    balance_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text())
    invoice_kind: Mapped[str] = mapped_column(String(20), nullable=False, server_default='receipt')
    hsn_sac: Mapped[str | None] = mapped_column(String(12))
    buyer_gstin: Mapped[str | None] = mapped_column(String(15))
    place_of_supply_state: Mapped[str | None] = mapped_column(String(2))
    supply_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default='exempt')
    tax_inclusive: Mapped[bool] = mapped_column(nullable=False, server_default='false')
    taxable_value_inr: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default='0'
    )
    gst_rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, server_default='0')
    cgst_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default='0')
    sgst_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default='0')
    igst_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default='0')
    total_tax_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default='0')

    __table_args__ = (
        UniqueConstraint('unit_id', 'invoice_no', name='uq_mitra_billing_invoice'),
        CheckConstraint(
            "invoice_kind IN ('tax_invoice','bill_of_supply','receipt')",
            name='ck_mitra_billing_invoice_kind',
        ),
        CheckConstraint(
            "supply_type IN ('intra_state','inter_state','exempt')",
            name='ck_mitra_billing_supply_type',
        ),
        Index('ix_mitra_billing_sales_unit_date', 'unit_id', 'sale_date'),
        CheckConstraint('amount_inr >= 0', name='ck_mitra_billing_amount_nonneg'),
        CheckConstraint('received_inr >= 0', name='ck_mitra_billing_received_nonneg'),
        CheckConstraint('balance_inr >= 0', name='ck_mitra_billing_balance_nonneg'),
    )


class MitraExpense(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = 'mitra_expenses'

    unit_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey('mitra_farm_units.id', ondelete='CASCADE'), nullable=False, index=True
    )
    expense_date: Mapped[date] = mapped_column(Date(), nullable=False)
    bill_no: Mapped[str | None] = mapped_column(String(64))
    flock_batch_ref: Mapped[str | None] = mapped_column(String(64))
    supplier: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    amount_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    paid_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    balance_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text())

    __table_args__ = (
        UniqueConstraint('unit_id', 'bill_no', name='uq_mitra_expense_bill'),
        Index('ix_mitra_expenses_unit_date', 'unit_id', 'expense_date'),
        CheckConstraint('amount_inr > 0', name='ck_mitra_expense_amount_pos'),
        CheckConstraint('paid_inr >= 0', name='ck_mitra_expense_paid_nonneg'),
        CheckConstraint('balance_inr >= 0', name='ck_mitra_expense_balance_nonneg'),
    )


class MitraUnitBillingSettings(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = 'mitra_unit_billing_settings'

    unit_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey('mitra_farm_units.id', ondelete='CASCADE'),
        nullable=False,
        unique=True,
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True
    )
    is_gst_registered: Mapped[bool] = mapped_column(nullable=False, server_default='false')
    seller_gstin: Mapped[str | None] = mapped_column(String(15))
    seller_legal_name: Mapped[str | None] = mapped_column(String(200))
    seller_state_code: Mapped[str | None] = mapped_column(String(2))
    default_place_of_supply_state: Mapped[str | None] = mapped_column(String(2))
    invoice_series_prefix: Mapped[str | None] = mapped_column(String(20))
    late_payment_interest_pct_annual: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default='0'
    )
    default_gst_rate_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    __table_args__ = (
        CheckConstraint(
            'late_payment_interest_pct_annual >= 0 AND late_payment_interest_pct_annual <= 100',
            name='ck_mitra_billing_settings_interest',
        ),
    )


class MitraGstRateSlab(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = 'mitra_gst_rate_slabs'

    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True
    )
    hsn_sac: Mapped[str] = mapped_column(String(12), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    gst_rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date())
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default='true')
    notes: Mapped[str | None] = mapped_column(Text())

    __table_args__ = (
        UniqueConstraint('owner_user_id', 'hsn_sac', name='uq_mitra_gst_slab_owner_hsn'),
        CheckConstraint('gst_rate_pct >= 0 AND gst_rate_pct <= 100', name='ck_mitra_gst_slab_rate'),
    )
