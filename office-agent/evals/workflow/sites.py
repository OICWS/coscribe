"""The sites, the data each task files on them, and the two ways a task is built: fixed steps
as a person would record them on the original site, and one bounded agent step per row."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from .site import Field, SiteSpec


def _expense_check(page: int, values: dict[str, str]) -> list[str]:
    out = []
    if page == 0 and not values.get("email", "").endswith("@acme.example"):
        out.append("Use your @acme.example work email.")
    if page == 1:
        try:
            if not 0 < float(values.get("amount", "")) <= 5000:
                out.append("Amount must be between 0 and 5000.")
        except ValueError:
            out.append("Amount must be a number.")
    return out


def _supplier_check(page: int, values: dict[str, str]) -> list[str]:
    out = []
    if page == 0 and not values.get("vat", "").startswith(values.get("country", "??")[:2].upper()):
        out.append("The VAT number starts with the country code.")
    if page == 1 and len(values.get("iban", "").replace(" ", "")) < 15:
        out.append("That IBAN is too short.")
    return out


EXPENSES = SiteSpec(
    slug="expenses",
    title="Acme Expenses",
    pages=(
        (
            Field("name", "Full name", "Name (as on payslip)", column="name"),
            Field("emp", "Employee ID", "Staff number", column="employee_id"),
            Field("email", "Work email", "Email address", "email", column="email"),
            Field("dept", "Department", "Team", "select",
                  ("Finance", "Marketing", "Operations", "Sales"), column="department"),
        ),
        (
            Field("date", "Date of expense", "When did you pay?", "date", column="date"),
            Field("cat", "Category", "Type of expense", "radio",
                  ("Travel", "Meals", "Equipment"), column="category"),
            Field("amount", "Amount (EUR)", "Total in euros", "number", column="amount"),
            Field("desc", "What was it for?", "Business purpose", "textarea", column="description"),
            Field("receipt", "I have the receipt", "I can provide a receipt", "checkbox",
                  column="receipt"),
        ),
    ),
    next_label=("Next", "Continue"),
    submit_label=("Submit claim", "Send claim"),
    done_heading="Claim submitted",
    ref_prefix=("EXP", "ACME"),
    extra=Field("centre", "Cost centre", "Cost centre", "select",
                ("CC-100 Head office", "CC-200 Hamburg", "CC-300 Munich"), column="cost_centre"),
    check=_expense_check,
)  # fmt: skip

SUPPLIERS = SiteSpec(
    slug="suppliers",
    title="Northwind Supplier Portal",
    pages=(
        (
            Field("company", "Company name", "Legal name of the business", column="company"),
            Field("country", "Country", "Registered in", "select",
                  ("DE", "FR", "NL", "PL"), column="country"),
            Field("vat", "VAT number", "Tax ID (VAT)", column="vat"),
            Field("contact", "Contact email", "Who we write to", "email", column="contact"),
        ),
        (
            Field("iban", "IBAN", "Bank account (IBAN)", column="iban"),
            Field("terms", "Payment terms", "When we pay you", "radio",
                  ("30 days", "60 days"), column="terms"),
            Field("start", "First delivery", "Expected first delivery", "date",
                  column="first_delivery"),
            Field("ok", "The details above are correct", "I confirm these details", "checkbox"),
        ),
    ),
    next_label=("Next", "Save and continue"),
    submit_label=("Register supplier", "Finish registration"),
    done_heading="Supplier registered",
    ref_prefix=("SUP", "NW"),
    extra=Field("category", "Supplier category", "What you supply", "select",
                ("Raw materials", "Logistics", "Services"), column="category"),
    check=_supplier_check,
)  # fmt: skip

SITES = {s.slug: s for s in (EXPENSES, SUPPLIERS)}

ROWS: dict[str, list[dict[str, str]]] = {
    "expenses": [
        {"name": "Tobias Renner", "employee_id": "E-40921", "email": "tobias.renner@acme.example",
         "department": "Operations", "date": "2026-11-02", "category": "Equipment",
         "amount": "1180.00", "description": "Five barcode scanners for Hamburg (quote B)",
         "receipt": "yes", "cost_centre": "CC-200 Hamburg"},
        {"name": "Maria Keller", "employee_id": "E-20417", "email": "maria.keller@acme.example",
         "department": "Sales", "date": "2026-11-04", "category": "Meals", "amount": "54.20",
         "description": "Dinner with the Bauer procurement team", "receipt": "yes",
         "cost_centre": "CC-100 Head office"},
    ],
    "suppliers": [
        {"company": "Brandt Logistik GmbH", "country": "DE", "vat": "DE811907980",
         "contact": "ap@brandt-logistik.example", "iban": "DE89 3704 0044 0532 0130 00",
         "terms": "30 days", "first_delivery": "2026-12-01", "category": "Logistics"},
        {"company": "Atelier Moreau SARL", "country": "FR", "vat": "FR40303265045",
         "contact": "compta@moreau.example", "iban": "FR14 2004 1010 0505 0001 3M02 606",
         "terms": "60 days", "first_delivery": "2027-01-15", "category": "Services"},
    ],
}  # fmt: skip


def sheet(slug: str, *, extra_column: bool) -> str:
    """The CSV a task reads; the extra field's column only when the scenario gives it."""
    spec = SITES[slug]
    rows = [dict(r) for r in ROWS[slug]]
    if not extra_column:
        for r in rows:
            r.pop(spec.extra.column, None)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def _read_steps(path: str) -> list[dict[str, Any]]:
    return [
        {"id": "read", "kind": "tool", "title": "Read the sheet", "tool": "read_file",
         "args": {"path": path}, "save_as": "raw"},
        {"id": "parse", "kind": "script", "title": "Turn the sheet into rows",
         "code": "import csv, io, json\n"
                 "print(json.dumps(list(csv.DictReader(io.StringIO(inputs['raw'])))))",
         "inputs": {"raw": "{{raw}}"}, "save_as": "rows"},
    ]  # fmt: skip


def _field_steps(f: Field, n: int) -> list[dict[str, Any]]:
    """The step a person recording the original site gets for one field. A date is set through
    the page, since typing into a date input doesn't take."""
    value = "{{row." + f.column + "}}"
    sid = f"{f.name}{n}"
    if f.kind == "select":
        return [
            {
                "id": sid,
                "kind": "tool",
                "title": f"Pick {f.label}",
                "tool": "browser_select_option",
                "args": {"ref": f'combobox "{f.label}"', "values": [value]},
            }
        ]
    if f.kind == "radio":
        return [
            {
                "id": sid,
                "kind": "tool",
                "title": f"Choose {f.label}",
                "tool": "browser_click",
                "args": {"ref": f'radio "{value}"'},
            }
        ]
    if f.kind == "checkbox":
        return [
            {
                "id": sid,
                "kind": "tool",
                "title": f"Tick {f.label}",
                "tool": "browser_click",
                "args": {"ref": f'checkbox "{f.label}"'},
            }
        ]
    if f.kind == "date":
        js = (
            f"(()=>{{const e=document.querySelector('[name={f.name}]');e.value='{value}';"
            "e.dispatchEvent(new Event('change',{bubbles:true}));return e.value})()"
        )
        return [
            {
                "id": sid,
                "kind": "tool",
                "title": f"Enter {f.label}",
                "tool": "browser_evaluate",
                "args": {"expression": js},
            }
        ]
    role = {"number": "spinbutton", "textarea": "textbox"}.get(f.kind, "textbox")
    return [{"id": sid, "kind": "tool", "title": f"Enter {f.label}", "tool": "browser_type",
             "args": {"ref": f'{role} "{f.label}"', "text": value}}]  # fmt: skip


def fixed_workflow(
    spec: SiteSpec, base: str, path: str, *, mark_submit: bool = False
) -> dict[str, Any]:
    """`mark_submit`: mark the final submit irreversible (decision 0029), so a repair's test
    runs stop before it."""
    steps: list[dict[str, Any]] = [
        {"id": "open", "kind": "tool", "title": "Open the form", "tool": "browser_navigate",
         "args": {"url": f"{base}/form"}},
    ]  # fmt: skip
    for n, page in enumerate(spec.pages):
        for f in page:
            steps += _field_steps(f, n)
        last = n == len(spec.pages) - 1
        label = spec.submit_label[0] if last else spec.next_label[0]
        button: dict[str, Any] = {"id": f"button{n}", "kind": "tool", "title": f"Click {label}",
                  "tool": "browser_click", "args": {"ref": f'button "{label}"'}}  # fmt: skip
        if last and mark_submit:
            button["irreversible"] = True
        steps.append(button)
    steps += [
        {"id": "done", "kind": "tool", "title": "Wait for the reference",
         "tool": "browser_wait_for",
         "args": {"text": "Your reference is"}},
        {"id": "ref", "kind": "tool", "title": "Read the reference", "tool": "browser_evaluate",
         "args": {"expression": "document.getElementById('ref').textContent"}, "save_as": "ref"},
    ]  # fmt: skip
    return {
        "version": 1,
        "inputs": [],
        "steps": [
            *_read_steps(path),
            {"id": "each", "kind": "loop", "title": "File each row", "over": "rows", "item": "row",
             "steps": steps, "collect": "ref", "save_as": "refs", "max_items": 10},
        ],
    }  # fmt: skip


def agent_workflow(spec: SiteSpec, base: str, path: str) -> dict[str, Any]:
    goal = (
        f"Fill in and send the form at {base}/form ({spec.title}) for this row, so that it is "
        "filed, not just saved:\n{{row}}\n\nUse exactly these values; tick any box that confirms "
        "the entry. If the site asks for something the row doesn't give, don't guess: stop and "
        "say what is missing. If the site rejects something, read its message and fix it. "
        "Return the reference the site gives."
    )
    step = {
        "id": "file", "kind": "agent", "title": f"File the row in {spec.title}",
        "autonomy": "bounded", "goal": goal,
        "tools": ["browser_navigate", "browser_snapshot", "browser_type", "browser_click",
                  "browser_select_option", "browser_wait_for", "browser_evaluate"],
        "sites": ["127.0.0.1"], "fields": [{"name": "reference", "type": "text"}],
        "max_steps": 30, "save_as": "filed", "trusted_input": True,
    }  # fmt: skip
    return {
        "version": 2,
        "inputs": [],
        "steps": [
            *_read_steps(path),
            {"id": "each", "kind": "loop", "title": "File each row", "over": "rows", "item": "row",
             "steps": [step], "collect": "filed", "save_as": "refs", "max_items": 10},
        ],
    }  # fmt: skip


def expected(slug: str, *, extra: bool) -> list[dict[str, str]]:
    """What the site must hold once every row is filed, by form field."""
    spec = SITES[slug]
    fields = [f for page in spec.pages for f in page] + ([spec.extra] if extra else [])
    return [{f.name: (r[f.column] if f.column else "yes") for f in fields} for r in ROWS[slug]]


def dumps(workflow: dict[str, Any]) -> str:
    return json.dumps(workflow, ensure_ascii=False, indent=1)
