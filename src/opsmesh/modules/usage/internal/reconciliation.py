"""Compare immutable provider invoice lines without billing the same usage again."""

import hashlib
import json
from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.usage.api import CostRecord, InvoiceReconciliation, ProviderInvoiceLine


def reconcile_line(data: dict[str, Any], line: ProviderInvoiceLine) -> InvoiceReconciliation:
    identity = hashlib.sha256(
        json.dumps([line.provider, line.invoice_id, line.line_id]).encode()
    ).hexdigest()
    invoices = data.setdefault("invoices", {})
    prior = invoices.get(identity)
    payload = line.model_dump(mode="json")
    if prior is not None and prior["line"] != payload:
        raise OpsMeshError("CONFLICT", "Provider invoice line identity has different values")
    observation = data["observations"].get(line.call_id)
    if observation is None:
        result = InvoiceReconciliation(line=line, status="missing_usage")
    else:
        cost = CostRecord.model_validate(observation["cost"])
        currency = cost.source_currency or cost.currency
        amount = cost.source_amount if cost.source_amount is not None else cost.amount
        if amount is None:
            result = InvoiceReconciliation(line=line, status="unpriced")
        elif currency != line.currency:
            result = InvoiceReconciliation(line=line, status="currency_mismatch")
        else:
            delta = line.amount - amount
            result = InvoiceReconciliation(
                line=line,
                status="matched" if delta == 0 else "difference",
                recorded_amount=amount,
                difference=delta,
            )
    invoices[identity] = result.model_dump(mode="json")
    return result
