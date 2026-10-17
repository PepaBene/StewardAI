"""The XBRL concepts the statement fact store keeps, and how they are classified.

One catalog serves the SEC instance reader (`steward.sources.xbrl`), the ESEF reader, the SEC
Financial Statement and Notes backfill and the peer-metric derivation, so a concept is
spelled once. Every entry is `concept -> (statement, line_item)`; `statement` is one of the
values the `financial_statement_fact.statement` CHECK admits and `line_item` is the canonical
name a reader groups by. A concept with any dimension is a breakdown and is stored under the
statement `segment` whatever its catalog statement.

The IFRS names are the full-IFRS taxonomy's; borrowings have no single stable pair of
current/non-current names across taxonomy releases, so every plausible element name is listed
(an element a filing does not use simply does not appear).
"""

from __future__ import annotations

import re

Statement = str

_ENTRIES: tuple[tuple[str, Statement, str, tuple[str, ...]], ...] = (
    # ------------------------------------------------------------------ income statement
    (
        "income",
        "income",
        "revenue",
        (
            "us-gaap:Revenues",
            "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
            "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
            "us-gaap:SalesRevenueNet",
            "ifrs-full:Revenue",
            "ifrs-full:RevenueFromContractsWithCustomers",
        ),
    ),
    (
        "income",
        "income",
        "cost_of_revenue",
        (
            "us-gaap:CostOfRevenue",
            "us-gaap:CostOfGoodsAndServicesSold",
            "us-gaap:CostOfGoodsSold",
            "ifrs-full:CostOfSales",
        ),
    ),
    ("income", "income", "gross_profit", ("us-gaap:GrossProfit", "ifrs-full:GrossProfit")),
    (
        "income",
        "income",
        "research_and_development",
        (
            "us-gaap:ResearchAndDevelopmentExpense",
            "us-gaap:ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
            "ifrs-full:ResearchAndDevelopmentExpense",
        ),
    ),
    (
        "income",
        "income",
        "selling_general_administrative",
        (
            "us-gaap:SellingGeneralAndAdministrativeExpense",
            "us-gaap:SellingAndMarketingExpense",
            "us-gaap:GeneralAndAdministrativeExpense",
            "ifrs-full:SellingGeneralAndAdministrativeExpense",
            "ifrs-full:DistributionCosts",
            "ifrs-full:AdministrativeExpense",
        ),
    ),
    (
        "income",
        "income",
        "operating_expenses",
        ("us-gaap:OperatingExpenses", "us-gaap:CostsAndExpenses"),
    ),
    (
        "income",
        "income",
        "operating_income",
        ("us-gaap:OperatingIncomeLoss", "ifrs-full:ProfitLossFromOperatingActivities"),
    ),
    (
        "income",
        "income",
        "interest_expense",
        (
            "us-gaap:InterestExpense",
            "us-gaap:InterestExpenseNonoperating",
            "us-gaap:InterestExpenseDebt",
            "ifrs-full:InterestExpense",
            "ifrs-full:FinanceCosts",
        ),
    ),
    (
        "income",
        "income",
        "interest_income",
        (
            "us-gaap:InvestmentIncomeInterest",
            "us-gaap:InterestIncomeOther",
            "ifrs-full:InterestRevenueExpense",
            "ifrs-full:FinanceIncome",
        ),
    ),
    (
        "income",
        "income",
        "pretax_income",
        (
            "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
            "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",
            "ifrs-full:ProfitLossBeforeTax",
        ),
    ),
    (
        "income",
        "income",
        "income_tax",
        ("us-gaap:IncomeTaxExpenseBenefit", "ifrs-full:IncomeTaxExpenseContinuingOperations"),
    ),
    (
        "income",
        "income",
        "net_income",
        (
            "us-gaap:NetIncomeLoss",
            "us-gaap:ProfitLoss",
            "us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic",
            "ifrs-full:ProfitLoss",
            "ifrs-full:ProfitLossAttributableToOwnersOfParent",
        ),
    ),
    (
        "income",
        "income",
        "eps_basic",
        ("us-gaap:EarningsPerShareBasic", "ifrs-full:BasicEarningsLossPerShare"),
    ),
    (
        "income",
        "income",
        "eps_diluted",
        ("us-gaap:EarningsPerShareDiluted", "ifrs-full:DilutedEarningsLossPerShare"),
    ),
    (
        "income",
        "income",
        "weighted_shares_basic",
        (
            "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic",
            "ifrs-full:WeightedAverageShares",
        ),
    ),
    (
        "income",
        "income",
        "weighted_shares_diluted",
        ("us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",),
    ),
    (
        "income",
        "income",
        "dividends_per_share",
        (
            "us-gaap:CommonStockDividendsPerShareDeclared",
            "us-gaap:CommonStockDividendsPerShareCashPaid",
            "ifrs-full:DividendsRecognisedAsDistributionsToOwnersPerShare",
        ),
    ),
    # --------------------------------------------------------------------- balance sheet
    ("balance", "balance_sheet", "total_assets", ("us-gaap:Assets", "ifrs-full:Assets")),
    (
        "balance",
        "balance_sheet",
        "current_assets",
        ("us-gaap:AssetsCurrent", "ifrs-full:CurrentAssets"),
    ),
    (
        "balance",
        "balance_sheet",
        "total_liabilities",
        ("us-gaap:Liabilities", "ifrs-full:Liabilities"),
    ),
    (
        "balance",
        "balance_sheet",
        "current_liabilities",
        ("us-gaap:LiabilitiesCurrent", "ifrs-full:CurrentLiabilities"),
    ),
    (
        "balance",
        "balance_sheet",
        "total_liabilities_and_equity",
        ("us-gaap:LiabilitiesAndStockholdersEquity", "ifrs-full:EquityAndLiabilities"),
    ),
    (
        "balance",
        "balance_sheet",
        "equity",
        (
            "us-gaap:StockholdersEquity",
            "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "ifrs-full:EquityAttributableToOwnersOfParent",
            "ifrs-full:Equity",
        ),
    ),
    (
        "balance",
        "balance_sheet",
        "retained_earnings",
        (
            "us-gaap:RetainedEarningsAccumulatedDeficit",
            "ifrs-full:RetainedEarnings",
        ),
    ),
    (
        "balance",
        "balance_sheet",
        "noncontrolling_interest",
        ("us-gaap:MinorityInterest", "ifrs-full:NoncontrollingInterests"),
    ),
    (
        "balance",
        "balance_sheet",
        "cash",
        (
            "us-gaap:CashAndCashEquivalentsAtCarryingValue",
            "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "ifrs-full:CashAndCashEquivalents",
        ),
    ),
    (
        "balance",
        "balance_sheet",
        "receivables",
        ("us-gaap:AccountsReceivableNetCurrent", "ifrs-full:TradeAndOtherCurrentReceivables"),
    ),
    ("balance", "balance_sheet", "inventory", ("us-gaap:InventoryNet", "ifrs-full:Inventories")),
    (
        "balance",
        "balance_sheet",
        "property_plant_equipment",
        ("us-gaap:PropertyPlantAndEquipmentNet", "ifrs-full:PropertyPlantAndEquipment"),
    ),
    ("balance", "balance_sheet", "goodwill", ("us-gaap:Goodwill", "ifrs-full:Goodwill")),
    (
        "balance",
        "balance_sheet",
        "intangibles",
        (
            "us-gaap:IntangibleAssetsNetExcludingGoodwill",
            "ifrs-full:IntangibleAssetsOtherThanGoodwill",
        ),
    ),
    (
        "balance",
        "balance_sheet",
        "payables",
        ("us-gaap:AccountsPayableCurrent", "ifrs-full:TradeAndOtherCurrentPayables"),
    ),
    # ---------------------------------------------------------------------------- debt
    ("debt", "balance_sheet", "long_term_debt_total", ("us-gaap:LongTermDebt",)),
    (
        "debt",
        "balance_sheet",
        "long_term_debt_current",
        ("us-gaap:LongTermDebtCurrent", "us-gaap:DebtCurrent"),
    ),
    ("debt", "balance_sheet", "long_term_debt_noncurrent", ("us-gaap:LongTermDebtNoncurrent",)),
    ("debt", "balance_sheet", "short_term_borrowings", ("us-gaap:ShortTermBorrowings",)),
    ("debt", "balance_sheet", "commercial_paper", ("us-gaap:CommercialPaper",)),
    ("debt", "balance_sheet", "borrowings_total", ("ifrs-full:Borrowings",)),
    (
        "debt",
        "balance_sheet",
        "borrowings_current",
        (
            "ifrs-full:CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
            "ifrs-full:CurrentBorrowings",
            "ifrs-full:BorrowingsCurrent",
            "ifrs-full:CurrentPortionOfLongtermBorrowings",
        ),
    ),
    (
        "debt",
        "balance_sheet",
        "borrowings_noncurrent",
        (
            "ifrs-full:LongtermBorrowings",
            "ifrs-full:NoncurrentBorrowings",
            "ifrs-full:NoncurrentPortionOfNoncurrentBorrowings",
            "ifrs-full:BorrowingsNoncurrent",
        ),
    ),
    (
        "debt",
        "note",
        "debt_maturity_y1",
        ("us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",),
    ),
    (
        "debt",
        "note",
        "debt_maturity_y2",
        ("us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo",),
    ),
    (
        "debt",
        "note",
        "debt_maturity_y3",
        ("us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInYearThree",),
    ),
    (
        "debt",
        "note",
        "debt_maturity_y4",
        ("us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFour",),
    ),
    (
        "debt",
        "note",
        "debt_maturity_y5",
        ("us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFive",),
    ),
    (
        "debt",
        "note",
        "debt_maturity_after_y5",
        ("us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive",),
    ),
    (
        "debt",
        "note",
        "debt_maturity_remainder",
        ("us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalRemainderOfFiscalYear",),
    ),
    # --------------------------------------------------------------------------- leases
    (
        "lease",
        "balance_sheet",
        "lease_liability",
        (
            "us-gaap:OperatingLeaseLiability",
            "us-gaap:FinanceLeaseLiability",
            "ifrs-full:LeaseLiabilities",
        ),
    ),
    (
        "lease",
        "balance_sheet",
        "lease_liability_current",
        (
            "us-gaap:OperatingLeaseLiabilityCurrent",
            "us-gaap:FinanceLeaseLiabilityCurrent",
            "ifrs-full:CurrentLeaseLiabilities",
        ),
    ),
    (
        "lease",
        "balance_sheet",
        "lease_liability_noncurrent",
        (
            "us-gaap:OperatingLeaseLiabilityNoncurrent",
            "us-gaap:FinanceLeaseLiabilityNoncurrent",
            "ifrs-full:NoncurrentLeaseLiabilities",
        ),
    ),
    (
        "lease",
        "balance_sheet",
        "right_of_use_asset",
        (
            "us-gaap:OperatingLeaseRightOfUseAsset",
            "us-gaap:FinanceLeaseRightOfUseAsset",
            "ifrs-full:RightofuseAssets",
        ),
    ),
    ("lease", "note", "lease_cost", ("us-gaap:OperatingLeaseCost", "us-gaap:LeaseCost")),
    (
        "lease",
        "note",
        "lease_payments_due_y1",
        (
            "us-gaap:LesseeOperatingLeaseLiabilityPaymentsDueNextTwelveMonths",
            "us-gaap:FinanceLeaseLiabilityPaymentsDueNextTwelveMonths",
        ),
    ),
    (
        "lease",
        "note",
        "lease_payments_due_y2",
        (
            "us-gaap:LesseeOperatingLeaseLiabilityPaymentsDueYearTwo",
            "us-gaap:FinanceLeaseLiabilityPaymentsDueYearTwo",
        ),
    ),
    (
        "lease",
        "note",
        "lease_payments_due_after_y5",
        (
            "us-gaap:LesseeOperatingLeaseLiabilityPaymentsDueAfterYearFive",
            "us-gaap:FinanceLeaseLiabilityPaymentsDueAfterYearFive",
        ),
    ),
    # ---------------------------------------------------------------------------- taxes
    (
        "tax",
        "note",
        "income_tax_current",
        ("us-gaap:CurrentIncomeTaxExpenseBenefit", "ifrs-full:CurrentTaxExpenseIncome"),
    ),
    (
        "tax",
        "note",
        "income_tax_deferred",
        ("us-gaap:DeferredIncomeTaxExpenseBenefit", "ifrs-full:DeferredTaxExpenseIncome"),
    ),
    (
        "tax",
        "note",
        "effective_tax_rate",
        (
            "us-gaap:EffectiveIncomeTaxRateContinuingOperations",
            "ifrs-full:AverageEffectiveTaxRate",
        ),
    ),
    (
        "tax",
        "note",
        "income_taxes_paid",
        (
            "us-gaap:IncomeTaxesPaidNet",
            "us-gaap:IncomeTaxesPaid",
            "ifrs-full:IncomeTaxesPaidRefundClassifiedAsOperatingActivities",
        ),
    ),
    (
        "tax",
        "note",
        "deferred_tax_assets",
        (
            "us-gaap:DeferredTaxAssetsNet",
            "us-gaap:DeferredIncomeTaxAssetsNet",
            "ifrs-full:DeferredTaxAssets",
        ),
    ),
    (
        "tax",
        "note",
        "deferred_tax_liabilities",
        (
            "us-gaap:DeferredTaxLiabilities",
            "us-gaap:DeferredIncomeTaxLiabilitiesNet",
            "ifrs-full:DeferredTaxLiabilities",
        ),
    ),
    ("tax", "note", "unrecognized_tax_benefits", ("us-gaap:UnrecognizedTaxBenefits",)),
    # ----------------------------------------------------------------------- cash flow
    (
        "cash_flow",
        "cash_flow",
        "cash_from_operations",
        (
            "us-gaap:NetCashProvidedByUsedInOperatingActivities",
            "ifrs-full:CashFlowsFromUsedInOperatingActivities",
        ),
    ),
    (
        "cash_flow",
        "cash_flow",
        "cash_from_investing",
        (
            "us-gaap:NetCashProvidedByUsedInInvestingActivities",
            "ifrs-full:CashFlowsFromUsedInInvestingActivities",
        ),
    ),
    (
        "cash_flow",
        "cash_flow",
        "cash_from_financing",
        (
            "us-gaap:NetCashProvidedByUsedInFinancingActivities",
            "ifrs-full:CashFlowsFromUsedInFinancingActivities",
        ),
    ),
    (
        "cash_flow",
        "cash_flow",
        "capex",
        (
            "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
            "us-gaap:PaymentsToAcquireProductiveAssets",
            "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
            "ifrs-full:PurchaseOfPropertyPlantAndEquipment",
        ),
    ),
    (
        "cash_flow",
        "cash_flow",
        "acquisitions_paid",
        (
            "us-gaap:PaymentsToAcquireBusinessesNetOfCashAcquired",
            "us-gaap:PaymentsToAcquireBusinessesGross",
            "ifrs-full:CashFlowsUsedInObtainingControlOfSubsidiariesOrOtherBusinessesClassifiedAsInvestingActivities",
        ),
    ),
    (
        "cash_flow",
        "cash_flow",
        "divestiture_proceeds",
        (
            "us-gaap:ProceedsFromDivestitureOfBusinessesNetOfCashDivested",
            "us-gaap:ProceedsFromDivestitureOfBusinesses",
            "us-gaap:ProceedsFromSaleOfBusinessAssetsAndInterest",
            "ifrs-full:CashFlowsFromLosingControlOfSubsidiariesOrOtherBusinessesClassifiedAsInvestingActivities",
        ),
    ),
    (
        "cash_flow",
        "cash_flow",
        "depreciation_amortization",
        (
            "us-gaap:DepreciationDepletionAndAmortization",
            "us-gaap:DepreciationAndAmortization",
            "us-gaap:DepreciationAmortizationAndAccretionNet",
            "ifrs-full:DepreciationAndAmortisationExpense",
            "ifrs-full:AdjustmentsForDepreciationAndAmortisationExpense",
        ),
    ),
    # Many filers (Alphabet, AMD, Broadcom, Intuit, Oracle) tag the two halves separately and no
    # combined line; the statement view adds them up when the combined one is missing.
    (
        "cash_flow",
        "cash_flow",
        "depreciation",
        ("us-gaap:Depreciation", "ifrs-full:DepreciationExpense"),
    ),
    # IFRS filers (AstraZeneca) may tag only property, plant and equipment, with right-of-use
    # assets apart (IFRS 16); the statement view adds the two when no total is filed.
    (
        "cash_flow",
        "cash_flow",
        "depreciation_ppe",
        ("ifrs-full:DepreciationPropertyPlantAndEquipment",),
    ),
    (
        "cash_flow",
        "cash_flow",
        "depreciation_right_of_use",
        ("ifrs-full:DepreciationRightofuseAssets",),
    ),
    (
        "cash_flow",
        "cash_flow",
        "amortization_of_intangibles",
        (
            "us-gaap:AmortizationOfIntangibleAssets",
            "ifrs-full:AmortisationIntangibleAssetsOtherThanGoodwill",
            "ifrs-full:AmortisationExpense",
        ),
    ),
    (
        "cash_flow",
        "cash_flow",
        "share_based_compensation",
        ("us-gaap:ShareBasedCompensation", "ifrs-full:AdjustmentsForSharebasedPayments"),
    ),
    (
        "cash_flow",
        "cash_flow",
        "debt_issued",
        (
            "us-gaap:ProceedsFromIssuanceOfLongTermDebt",
            "us-gaap:ProceedsFromIssuanceOfDebt",
            "ifrs-full:ProceedsFromBorrowingsClassifiedAsFinancingActivities",
        ),
    ),
    (
        "cash_flow",
        "cash_flow",
        "debt_repaid",
        (
            "us-gaap:RepaymentsOfLongTermDebt",
            "us-gaap:RepaymentsOfDebt",
            "ifrs-full:RepaymentsOfBorrowingsClassifiedAsFinancingActivities",
        ),
    ),
    # --------------------------------------------------------- repurchases and dividends
    (
        "capital_return",
        "cash_flow",
        "share_repurchases_paid",
        (
            "us-gaap:PaymentsForRepurchaseOfCommonStock",
            "ifrs-full:PaymentsToAcquireOrRedeemEntitysShares",
            "ifrs-full:PurchaseOfTreasuryShares",
        ),
    ),
    (
        "capital_return",
        "cash_flow",
        "dividends_paid",
        (
            "us-gaap:PaymentsOfDividends",
            "us-gaap:PaymentsOfDividendsCommonStock",
            "ifrs-full:DividendsPaidClassifiedAsFinancingActivities",
        ),
    ),
    (
        "capital_return",
        "note",
        "shares_repurchased",
        (
            "us-gaap:StockRepurchasedAndRetiredDuringPeriodShares",
            "us-gaap:StockRepurchasedDuringPeriodShares",
            "us-gaap:TreasuryStockSharesAcquired",
        ),
    ),
    (
        "capital_return",
        "note",
        "share_repurchase_value",
        (
            "us-gaap:StockRepurchasedAndRetiredDuringPeriodValue",
            "us-gaap:StockRepurchasedDuringPeriodValue",
        ),
    ),
    (
        "capital_return",
        "note",
        "repurchase_authorization",
        (
            "us-gaap:StockRepurchaseProgramAuthorizedAmount1",
            "us-gaap:StockRepurchaseProgramAuthorizedAmount",
            "us-gaap:StockRepurchaseProgramRemainingAuthorizedRepurchaseAmount",
        ),
    ),
    # ------------------------------------------------------- related-party totals (no dims)
    (
        "related_party",
        "note",
        "related_party_transactions",
        ("us-gaap:RelatedPartyTransactionAmountsOfTransaction",),
    ),
    (
        "related_party",
        "note",
        "related_party_revenue",
        ("us-gaap:RevenueFromRelatedParties",),
    ),
    (
        "related_party",
        "note",
        "related_party_costs",
        (
            "us-gaap:CostsAndExpensesRelatedParty",
            "us-gaap:RelatedPartyTransactionExpensesFromTransactionsWithRelatedParty",
        ),
    ),
    (
        "related_party",
        "note",
        "related_party_receivable",
        ("us-gaap:DueFromRelatedParties", "us-gaap:AccountsReceivableRelatedParties"),
    ),
    (
        "related_party",
        "note",
        "related_party_payable",
        ("us-gaap:DueToRelatedParties", "us-gaap:AccountsPayableRelatedParties"),
    ),
    # ------------------------------------------------------------------------ pensions
    (
        "pension",
        "note",
        "pension_obligation",
        (
            "us-gaap:DefinedBenefitPlanBenefitObligation",
            "ifrs-full:DefinedBenefitObligationAtPresentValue",
        ),
    ),
    (
        "pension",
        "note",
        "pension_plan_assets",
        (
            "us-gaap:DefinedBenefitPlanFairValueOfPlanAssets",
            "ifrs-full:FairValueOfPlanAssets",
        ),
    ),
    (
        "pension",
        "note",
        "pension_funded_status",
        (
            "us-gaap:DefinedBenefitPlanFundedStatusOfPlan",
            "ifrs-full:NetDefinedBenefitLiabilityAsset",
        ),
    ),
    (
        "pension",
        "note",
        "pension_net_cost",
        ("us-gaap:DefinedBenefitPlanNetPeriodicBenefitCostCreditExcludingAdjustments",),
    ),
    # ------------------------------------------------- segments and concentration (dims)
    (
        "segment",
        "segment",
        "segment_capex",
        ("us-gaap:SegmentExpenditureAdditionToLongLivedAssets",),
    ),
    (
        "segment",
        "segment",
        "customer_concentration",
        ("us-gaap:ConcentrationRiskPercentage", "us-gaap:ConcentrationRiskPercentage1"),
    ),
    # ------------------------------------------------------------------------ shares
    (
        "shares",
        "shares",
        "shares_outstanding",
        (
            "dei:EntityCommonStockSharesOutstanding",
            "us-gaap:CommonStockSharesOutstanding",
            "ifrs-full:NumberOfSharesOutstanding",
        ),
    ),
    ("shares", "shares", "shares_issued", ("us-gaap:CommonStockSharesIssued",)),
)

# The cover-page facts kept next to the statements. All are text-valued except where noted;
# the CIK in particular must stay a string ("0000320193"), never a number.
DOCUMENT_ITEMS: tuple[str, ...] = (
    "dei:DocumentType",
    "dei:DocumentPeriodEndDate",
    "dei:DocumentFiscalYearFocus",
    "dei:DocumentFiscalPeriodFocus",
    "dei:AmendmentFlag",
    "dei:AmendmentDescription",
    "dei:EntityCentralIndexKey",
    "dei:Security12bTitle",
    "dei:TradingSymbol",
    "dei:EntityRegistrantName",
    "dei:CurrentFiscalYearEndDate",
)

# A numeric disclosure is never filed under `note`: that statement is for text blocks (what
# `filed_notes` cites) and for detail facts with no line item. These line items belong to the
# statement they are a figure of.
_STATEMENT_OF: dict[str, Statement] = {
    **{f"debt_maturity_{y}": "balance_sheet" for y in ("y1", "y2", "y3", "y4", "y5")},
    "debt_maturity_after_y5": "balance_sheet",
    "debt_maturity_remainder": "balance_sheet",
    "lease_cost": "income",
    "lease_payments_due_y1": "balance_sheet",
    "lease_payments_due_y2": "balance_sheet",
    "lease_payments_due_after_y5": "balance_sheet",
    "income_tax_current": "income",
    "income_tax_deferred": "income",
    "effective_tax_rate": "income",
    "income_taxes_paid": "cash_flow",
    "deferred_tax_assets": "balance_sheet",
    "deferred_tax_liabilities": "balance_sheet",
    "unrecognized_tax_benefits": "balance_sheet",
    "shares_repurchased": "shares",
    "share_repurchase_value": "cash_flow",
    "repurchase_authorization": "balance_sheet",
    "pension_obligation": "balance_sheet",
    "pension_plan_assets": "balance_sheet",
    "pension_funded_status": "balance_sheet",
    "pension_net_cost": "income",
}

STATEMENT_ITEMS: dict[str, tuple[Statement, str]] = {
    concept: (_STATEMENT_OF.get(line_item, statement), line_item)
    for _group, statement, line_item, concepts in _ENTRIES
    for concept in concepts
}
STATEMENT_GROUP: dict[str, str] = {
    concept: group for group, _statement, _line_item, concepts in _ENTRIES for concept in concepts
}
STATEMENT_CONCEPTS: frozenset[str] = frozenset(STATEMENT_ITEMS)

# US-GAAP note topics whose text blocks are kept (a 10-K tags hundreds; these are the ones
# the statement reader and the analyst notes cite).
TEXT_BLOCK_TOPICS: tuple[str, ...] = (
    "debt",
    "borrowing",
    "lease",
    "incometax",
    "segment",
    "pension",
    "retirement",
    "postretirement",
    "sharebased",
    "compensation",
    "repurchase",
    "stockholdersequity",
    "equity",
    "revenue",
    "concentration",
    "researchanddevelopment",
    "commitment",
    "contingenc",
    "subsequentevent",
    "businesscombination",
    "goodwill",
    "derivative",
    "hedg",
    "restructuring",
    "inventor",
    "propertyplant",
    "earningspershare",
    "fairvalue",
)

# Equity tags that already include the non-controlling interest. A filer that tags only these
# has no parent-only equity, so the canonical `equity` must have the interest taken out before
# it is added or subtracted again.
EQUITY_INCLUDING_NCI: frozenset[str] = frozenset(
    {
        "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "ifrs-full:Equity",
    }
)

# Unit for monetary amounts: iso4217 code as the reader writes it, with an optional per-share
# or per-unit denominator ("USD/shares").
_CURRENCY = re.compile(r"^([A-Z]{3})(?:/[A-Za-z]+)?$")


# Pay versus performance (SEC Item 402(v), tagged in proxy statements with the `ecd` taxonomy):
# the principal executive officer's summary-table pay and pay actually paid, and the other named
# executives' averages (insight I13.4). Kept as notes; non-dimensional rows only (one PEO).
ECD_CONCEPTS: dict[str, str] = {
    "ecd:PeoTotalCompAmt": "peo_total_comp",
    "ecd:PeoActuallyPaidCompAmt": "peo_actually_paid_comp",
    "ecd:NonPeoNeoAvgTotalCompAmt": "neo_avg_total_comp",
    "ecd:NonPeoNeoAvgCompActuallyPaidAmt": "neo_avg_actually_paid_comp",
}


def classify(concept: str, *, dimensional: bool = False) -> tuple[Statement, str | None]:
    """(statement, line_item) of a concept; dimensional facts are `segment`."""
    if concept in ECD_CONCEPTS:
        return ("segment" if dimensional else "note"), ECD_CONCEPTS[concept]
    statement, line_item = STATEMENT_ITEMS.get(concept, ("note", None))
    if concept.startswith("dei:") and concept != "dei:EntityCommonStockSharesOutstanding":
        statement, line_item = "note", f"cover_{concept.split(':', 1)[1]}"
    if dimensional and statement != "shares":
        return "segment", line_item
    return statement, line_item


def is_wanted_text_block(concept: str) -> bool:
    """A TextBlock element of a note topic the store keeps."""
    local = concept.split(":", 1)[-1]
    if not local.endswith("TextBlock"):
        return False
    lowered = local.lower()
    return any(topic in lowered for topic in TEXT_BLOCK_TOPICS)


def currency_of(unit: str | None) -> str | None:
    """The ISO currency of a monetary unit ("USD", "EUR/shares"), else None."""
    if not unit:
        return None
    match = _CURRENCY.match(unit)
    return match.group(1) if match else None


# ------------------------------------------------------------------------- debt components

# Debt is the sum of components, never one element. For each taxonomy the *total* element
# (it already includes the current portion) wins; otherwise the non-current and current
# portions are added, using the first element present in each group so overlapping aliases
# are not added twice; short-term borrowings and commercial paper are separate instruments
# and are added on top. Lease liabilities are excluded (documented in the metric formula).
DEBT_TOTAL: dict[str, tuple[str, ...]] = {
    "us-gaap": ("longtermdebt",),
    "ifrs-full": ("borrowings",),
}
DEBT_PARTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "us-gaap": (
        ("longtermdebtnoncurrent",),
        ("longtermdebtcurrent",),
    ),
    "ifrs-full": (
        (
            "longtermborrowings",
            "noncurrentborrowings",
            "noncurrentportionofnoncurrentborrowings",
            "borrowingsnoncurrent",
        ),
        (
            "currentborrowingsandcurrentportionofnoncurrentborrowings",
            "currentborrowings",
            "borrowingscurrent",
            "currentportionoflongtermborrowings",
        ),
    ),
}
DEBT_EXTRA: dict[str, tuple[tuple[str, ...], ...]] = {
    "us-gaap": (("shorttermborrowings",), ("commercialpaper",)),
    "ifrs-full": (),
}
