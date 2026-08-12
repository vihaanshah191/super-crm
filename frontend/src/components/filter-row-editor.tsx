"use client";

import { FILTER_FIELDS, LIST_VALUE_OPERATORS, NO_VALUE_OPERATORS, OPERATOR_LABELS, fieldOption } from "@/lib/filter-fields";
import type { FilterOperator } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export interface FilterRowState {
  id: string;
  field: string;
  operator: FilterOperator;
  value: string;
  value2: string; // second bound, only used for BETWEEN
}

export function newFilterRow(id: string): FilterRowState {
  const first = FILTER_FIELDS[0];
  return { id, field: first.value, operator: first.operators[0], value: "", value2: "" };
}

const selectClassName =
  "h-9 rounded-md border border-input bg-transparent px-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50";

export function FilterRowEditor({
  row,
  onChange,
  onRemove,
  canRemove,
}: {
  row: FilterRowState;
  onChange: (next: FilterRowState) => void;
  onRemove: () => void;
  canRemove: boolean;
}) {
  const option = fieldOption(row.field);
  const needsValue = !NO_VALUE_OPERATORS.includes(row.operator);
  const isListValue = LIST_VALUE_OPERATORS.includes(row.operator);
  const isBetween = row.operator === "BETWEEN";

  function handleFieldChange(fieldName: string) {
    const next = fieldOption(fieldName);
    // Operator may no longer be valid for the new field -- fall back to
    // its first allowed operator rather than send an operator the field
    // doesn't support (the backend would reject it with a 422 anyway).
    const operator = next.operators.includes(row.operator) ? row.operator : next.operators[0];
    onChange({ ...row, field: fieldName, operator, value: "", value2: "" });
  }

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-border p-2">
      <select
        className={selectClassName}
        value={row.field}
        onChange={(e) => handleFieldChange(e.target.value)}
      >
        {FILTER_FIELDS.map((f) => (
          <option key={f.value} value={f.value}>
            {f.label}
          </option>
        ))}
      </select>

      <select
        className={selectClassName}
        value={row.operator}
        onChange={(e) => onChange({ ...row, operator: e.target.value as FilterOperator, value: "", value2: "" })}
      >
        {option.operators.map((op) => (
          <option key={op} value={op}>
            {OPERATOR_LABELS[op]}
          </option>
        ))}
      </select>

      {needsValue && option.inputKind === "boolean" && (
        <select className={selectClassName} value={row.value} onChange={(e) => onChange({ ...row, value: e.target.value })}>
          <option value="">select…</option>
          <option value="true">Yes</option>
          <option value="false">No</option>
        </select>
      )}

      {needsValue && option.inputKind === "select" && !isListValue && (
        <select className={selectClassName} value={row.value} onChange={(e) => onChange({ ...row, value: e.target.value })}>
          <option value="">select…</option>
          {option.selectOptions?.map((v) => (
            <option key={v} value={v}>
              {v.replace("_", " ")}
            </option>
          ))}
        </select>
      )}

      {needsValue && (option.inputKind === "text" || option.inputKind === "select") && (
        <Input
          className="h-9 w-40"
          placeholder={isListValue ? "comma-separated" : "value"}
          value={row.value}
          onChange={(e) => onChange({ ...row, value: e.target.value })}
        />
      )}

      {needsValue && option.inputKind === "number" && !isBetween && (
        <Input
          className="h-9 w-32"
          type="text"
          inputMode="decimal"
          placeholder={isListValue ? "comma-separated" : "value"}
          value={row.value}
          onChange={(e) => onChange({ ...row, value: e.target.value })}
        />
      )}

      {needsValue && option.inputKind === "date" && !isBetween && (
        <Input className="h-9 w-40" type="date" value={row.value} onChange={(e) => onChange({ ...row, value: e.target.value })} />
      )}

      {needsValue && isBetween && (
        <>
          <Input
            className="h-9 w-32"
            type={option.inputKind === "date" ? "date" : "text"}
            inputMode={option.inputKind === "number" ? "decimal" : undefined}
            placeholder="from"
            value={row.value}
            onChange={(e) => onChange({ ...row, value: e.target.value })}
          />
          <span className="text-sm text-muted-foreground">and</span>
          <Input
            className="h-9 w-32"
            type={option.inputKind === "date" ? "date" : "text"}
            inputMode={option.inputKind === "number" ? "decimal" : undefined}
            placeholder="to"
            value={row.value2}
            onChange={(e) => onChange({ ...row, value2: e.target.value })}
          />
        </>
      )}

      <Button type="button" variant="ghost" size="sm" onClick={onRemove} disabled={!canRemove}>
        Remove
      </Button>
    </div>
  );
}
