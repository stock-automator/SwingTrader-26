import { useMemo } from "react";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { fmtCurrency, fmtNum, fmtPct, signClass } from "../../lib/format";
import type { Position } from "../../types";

const columnHelper = createColumnHelper<Position>();

interface PositionsTableProps {
  positions: Position[];
}

export function PositionsTable({ positions }: PositionsTableProps) {
  const columns = useMemo(
    () => [
      columnHelper.accessor("symbol", { header: "Symbol" }),
      columnHelper.accessor("side", {
        header: "Side",
        cell: (info) => (
          <span
            className={
              info.getValue() === "short" ? "text-short" : "text-long"
            }
          >
            {(info.getValue() ?? "—").toUpperCase()}
          </span>
        ),
      }),
      columnHelper.accessor("qty", {
        header: "Qty",
        cell: (info) => fmtNum(info.getValue(), 0),
      }),
      columnHelper.accessor("avg_entry_price", {
        header: "Avg Entry",
        cell: (info) => fmtNum(info.getValue(), 2),
      }),
      columnHelper.accessor("current_price", {
        header: "Current",
        cell: (info) => fmtNum(info.getValue(), 2),
      }),
      columnHelper.accessor("market_value", {
        header: "Market Value",
        cell: (info) => fmtCurrency(info.getValue(), 2),
      }),
      columnHelper.accessor("unrealized_pl", {
        header: "Unrealized P&L",
        cell: (info) => (
          <span className={signClass(info.getValue())}>
            {fmtCurrency(info.getValue(), 2)}
          </span>
        ),
      }),
      columnHelper.accessor("unrealized_plpc", {
        header: "Unrealized %",
        cell: (info) => {
          const v = info.getValue();
          return (
            <span className={signClass(v)}>
              {fmtPct(v === null ? null : v * 100)}
            </span>
          );
        },
      }),
    ],
    [],
  );

  const table = useReactTable({
    data: positions,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  if (positions.length === 0) {
    return (
      <div
        data-testid="positions-empty"
        className="rounded border border-border bg-panel px-3 py-8 text-center text-sm text-text-faint"
      >
        No open positions.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded border border-border bg-panel">
      <table
        data-testid="positions-table"
        className="w-full min-w-[820px] border-collapse text-sm"
      >
        <thead>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr
              key={headerGroup.id}
              className="border-b border-border text-left text-[11px] uppercase tracking-wider text-text-faint"
            >
              {headerGroup.headers.map((header) => (
                <th key={header.id} className="px-3 py-2">
                  {flexRender(header.column.columnDef.header, header.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr
              key={row.id}
              data-testid="position-row"
              className="border-b border-border-soft last:border-0 hover:bg-panel-alt"
            >
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className="px-3 py-2">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
