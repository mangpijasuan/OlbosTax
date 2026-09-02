"""Synthetic taxpayers for development and testing.

Spec section 36: never use real taxpayer information in development.  Every
person here is invented.  The SSNs use the ``9xx-xx-xxxx`` range that the
Social Security Administration does not issue to individuals (900-999 is
reserved for ITINs and has never been assigned as an SSN), so none of these
values can collide with a real person's number.

Email addresses use ``example.com``, which IANA reserves for documentation and
which accepts no mail. The obvious alternative, ``.invalid``, is reserved by
RFC 2606 to be *permanently* invalid -- and is therefore rejected by the API's
email validation, correctly: a taxpayer who entered one would never receive
their filing status.

Each fixture is a plausible whole taxpayer rather than a minimal input,
because the bugs worth catching live in the interactions -- a W-2 plus a
1099-NEC plus a child, not a W-2 alone.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from olbostax_schema import (
    W2,
    Address,
    CreditsSection,
    DeductionsSection,
    Dependent,
    DependentRelationship,
    DirectDeposit,
    FilingStatus,
    Form1099DIV,
    Form1099G,
    Form1099INT,
    Form1099NEC,
    Form1099R,
    IncomeSection,
    OklahomaSection,
    PaymentsSection,
    Spouse,
    TaxReturnInput,
    Taxpayer,
)
from olbostax_schema.enums import AccountType

TAX_YEAR = 2025

_OK_ADDRESS = Address(
    line1="1200 N Robinson Ave",
    line2="Apt 14",
    city="Oklahoma City",
    state="OK",
    zip_code="73103",
)

# A real ABA routing number used by a large bank; the account number is
# invented. Routing numbers are public data and must pass the checksum, so a
# made-up one would fail validation.
_TEST_DEPOSIT = DirectDeposit(
    routing_number="103000648",
    account_number="000123456789",
    account_type=AccountType.CHECKING,
)


def synthetic_taxpayer_001() -> TaxReturnInput:
    """Single filer, one W-2, standard deduction. The simplest real return."""
    return TaxReturnInput(
        tax_year=TAX_YEAR,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Taxpayer(
            first_name="Dana",
            last_name="Whitfield",
            ssn="900-11-0001",
            date_of_birth=date(1992, 4, 18),
            email="dana.whitfield@example.com",
            phone="405-555-0101",
            address=_OK_ADDRESS,
        ),
        income=IncomeSection(
            w2s=[
                W2(
                    document_id="w2-001",
                    employer_name="Redbud Logistics LLC",
                    employer_ein="73-1234567",
                    box1_wages=Decimal("58240.12"),
                    box2_federal_income_tax_withheld=Decimal("5120.00"),
                    box3_social_security_wages=Decimal("58240.12"),
                    box4_social_security_tax_withheld=Decimal("3610.89"),
                    box5_medicare_wages=Decimal("58240.12"),
                    box6_medicare_tax_withheld=Decimal("844.48"),
                    box15_state="OK",
                    box16_state_wages=Decimal("58240.12"),
                    box17_state_income_tax=Decimal("1980.00"),
                )
            ],
            form_1099_ints=[
                Form1099INT(
                    document_id="1099int-001",
                    payer_name="Sooner Credit Union",
                    box1_interest_income=Decimal("312.44"),
                )
            ],
        ),
        direct_deposit=_TEST_DEPOSIT,
    )


def synthetic_married_family_001() -> TaxReturnInput:
    """Married filing jointly, two W-2s, two children, child care expenses."""
    return TaxReturnInput(
        tax_year=TAX_YEAR,
        filing_status=FilingStatus.MARRIED_FILING_JOINTLY,
        taxpayer=Taxpayer(
            first_name="Marcus",
            middle_initial="T",
            last_name="Okafor",
            ssn="900-22-0002",
            date_of_birth=date(1986, 9, 3),
            email="marcus.okafor@example.com",
            address=_OK_ADDRESS,
        ),
        spouse=Spouse(
            first_name="Priya",
            last_name="Okafor",
            ssn="900-22-0003",
            date_of_birth=date(1988, 1, 27),
        ),
        dependents=[
            Dependent(
                first_name="Amara",
                last_name="Okafor",
                ssn="900-22-0004",
                date_of_birth=date(2016, 6, 11),
                relationship=DependentRelationship.DAUGHTER,
                child_care_expenses_paid=True,
            ),
            Dependent(
                first_name="Ezra",
                last_name="Okafor",
                ssn="900-22-0005",
                date_of_birth=date(2020, 2, 29),
                relationship=DependentRelationship.SON,
                child_care_expenses_paid=True,
            ),
        ],
        income=IncomeSection(
            w2s=[
                W2(
                    document_id="w2-101",
                    employer_name="Chickasaw Regional Health",
                    box1_wages=Decimal("72500.00"),
                    box2_federal_income_tax_withheld=Decimal("6100.00"),
                    box3_social_security_wages=Decimal("72500.00"),
                    box5_medicare_wages=Decimal("72500.00"),
                    box15_state="OK",
                    box16_state_wages=Decimal("72500.00"),
                    box17_state_income_tax=Decimal("2600.00"),
                ),
                W2(
                    document_id="w2-102",
                    employer_name="Norman Public Schools",
                    employee_is_spouse=True,
                    box1_wages=Decimal("48900.00"),
                    box2_federal_income_tax_withheld=Decimal("3450.00"),
                    box3_social_security_wages=Decimal("48900.00"),
                    box5_medicare_wages=Decimal("48900.00"),
                    box15_state="OK",
                    box16_state_wages=Decimal("48900.00"),
                    box17_state_income_tax=Decimal("1620.00"),
                ),
            ],
            form_1099_divs=[
                Form1099DIV(
                    document_id="1099div-101",
                    payer_name="Prairie Index Fund",
                    box1a_total_ordinary_dividends=Decimal("1840.00"),
                    box1b_qualified_dividends=Decimal("1640.00"),
                    box2a_total_capital_gain_distributions=Decimal("420.00"),
                )
            ],
        ),
        credits=CreditsSection(
            child_care_expenses=Decimal("7800.00"),
            child_care_provider_count=1,
        ),
        direct_deposit=_TEST_DEPOSIT,
    )


def synthetic_self_employed_001() -> TaxReturnInput:
    """Head of household with a W-2 job and self-employment income.

    Exercises the Social Security wage base coordination between W-2 wages and
    self-employment earnings, which is a common real-world combination and an
    easy calculation to get wrong.
    """
    return TaxReturnInput(
        tax_year=TAX_YEAR,
        filing_status=FilingStatus.HEAD_OF_HOUSEHOLD,
        taxpayer=Taxpayer(
            first_name="Rosalind",
            last_name="Begay",
            ssn="900-33-0006",
            date_of_birth=date(1983, 12, 5),
            email="rosalind.begay@example.com",
            address=_OK_ADDRESS,
        ),
        dependents=[
            Dependent(
                first_name="Jonah",
                last_name="Begay",
                ssn="900-33-0007",
                date_of_birth=date(2011, 8, 22),
                relationship=DependentRelationship.SON,
            )
        ],
        income=IncomeSection(
            w2s=[
                W2(
                    document_id="w2-201",
                    employer_name="Tallgrass Design Co",
                    box1_wages=Decimal("41200.00"),
                    box2_federal_income_tax_withheld=Decimal("2980.00"),
                    box3_social_security_wages=Decimal("41200.00"),
                    box5_medicare_wages=Decimal("41200.00"),
                    box15_state="OK",
                    box17_state_income_tax=Decimal("1310.00"),
                )
            ],
            form_1099_necs=[
                Form1099NEC(
                    document_id="1099nec-201",
                    payer_name="Grand Lake Media",
                    box1_nonemployee_compensation=Decimal("18600.00"),
                    box6_state="OK",
                )
            ],
            self_employment_expenses=Decimal("3400.00"),
            self_employment_business_description="Freelance graphic design",
        ),
        direct_deposit=_TEST_DEPOSIT,
    )


def synthetic_retiree_001() -> TaxReturnInput:
    """Married retirees: Social Security, a pension, and the age-65 deduction.

    Exercises IRC s.86 provisional income, the additional standard deduction
    for age, and Oklahoma's full exemption of Social Security.
    """
    return TaxReturnInput(
        tax_year=TAX_YEAR,
        filing_status=FilingStatus.MARRIED_FILING_JOINTLY,
        taxpayer=Taxpayer(
            first_name="Harold",
            last_name="Lindqvist",
            ssn="900-44-0008",
            date_of_birth=date(1956, 3, 14),
            email="harold.lindqvist@example.com",
            address=_OK_ADDRESS,
        ),
        spouse=Spouse(
            first_name="Beatrice",
            last_name="Lindqvist",
            ssn="900-44-0009",
            date_of_birth=date(1958, 11, 2),
        ),
        income=IncomeSection(
            form_1099_rs=[
                Form1099R(
                    document_id="1099r-301",
                    payer_name="Oklahoma Teachers Retirement System",
                    box1_gross_distribution=Decimal("34800.00"),
                    box2a_taxable_amount=Decimal("34800.00"),
                    box4_federal_income_tax_withheld=Decimal("3100.00"),
                    box7_distribution_codes="7",
                    box15_state="OK",
                    box14_state_tax_withheld=Decimal("980.00"),
                )
            ],
            form_1099_ints=[
                Form1099INT(
                    document_id="1099int-301",
                    payer_name="First Fidelity Bank",
                    box1_interest_income=Decimal("2140.00"),
                    box3_interest_on_us_savings_bonds=Decimal("610.00"),
                )
            ],
            social_security_benefits_received=Decimal("41400.00"),
        ),
        oklahoma=OklahomaSection(
            oklahoma_government_retirement_benefits=Decimal("34800.00"),
        ),
        direct_deposit=_TEST_DEPOSIT,
    )


def synthetic_low_income_eitc_001() -> TaxReturnInput:
    """Single parent whose refund is driven by refundable credits.

    The taxpayer this product most needs to get right: total tax is zero, and
    the entire refund comes from the EITC and the Additional Child Tax Credit.
    An engine that applies credits in the wrong order shortchanges them.
    """
    return TaxReturnInput(
        tax_year=TAX_YEAR,
        filing_status=FilingStatus.HEAD_OF_HOUSEHOLD,
        taxpayer=Taxpayer(
            first_name="Tasha",
            last_name="Ramirez",
            ssn="900-55-0010",
            date_of_birth=date(1996, 7, 30),
            email="tasha.ramirez@example.com",
            address=_OK_ADDRESS,
        ),
        dependents=[
            Dependent(
                first_name="Mateo",
                last_name="Ramirez",
                ssn="900-55-0011",
                date_of_birth=date(2018, 5, 9),
                relationship=DependentRelationship.SON,
            ),
            Dependent(
                first_name="Sofia",
                last_name="Ramirez",
                ssn="900-55-0012",
                date_of_birth=date(2021, 10, 17),
                relationship=DependentRelationship.DAUGHTER,
            ),
        ],
        income=IncomeSection(
            w2s=[
                W2(
                    document_id="w2-401",
                    employer_name="Bricktown Hospitality Group",
                    box1_wages=Decimal("24150.00"),
                    box2_federal_income_tax_withheld=Decimal("380.00"),
                    box3_social_security_wages=Decimal("24150.00"),
                    box5_medicare_wages=Decimal("24150.00"),
                    box15_state="OK",
                    box17_state_income_tax=Decimal("410.00"),
                )
            ],
            form_1099_gs=[
                Form1099G(
                    document_id="1099g-401",
                    payer_name="Oklahoma Employment Security Commission",
                    box1_unemployment_compensation=Decimal("2800.00"),
                    box10a_state="OK",
                )
            ],
        ),
        oklahoma=OklahomaSection(
            claims_sales_tax_relief_credit=True,
            household_members_for_sales_tax_credit=3,
        ),
        direct_deposit=_TEST_DEPOSIT,
    )


ALL_SYNTHETIC_TAXPAYERS = {
    "SyntheticTaxpayer001": synthetic_taxpayer_001,
    "SyntheticMarriedFamily001": synthetic_married_family_001,
    "SyntheticSelfEmployed001": synthetic_self_employed_001,
    "SyntheticRetiree001": synthetic_retiree_001,
    "SyntheticLowIncomeEITC001": synthetic_low_income_eitc_001,
}
