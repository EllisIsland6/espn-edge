// Generic dense sortable table (SPEC 3: TanStack Table is the core table UI).
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useState } from "react";

export function DataTable<T>({
  data,
  columns,
  initialSort = [],
  rowClassName,
  columnClassName,
  ariaLabel,
}: {
  data: T[];
  columns: ColumnDef<T, any>[];
  initialSort?: SortingState;
  rowClassName?: (row: T) => string;
  columnClassName?: (columnId: string) => string;
  ariaLabel?: string;
}) {
  const [sorting, setSorting] = useState<SortingState>(initialSort);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm" aria-label={ariaLabel}>
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id} className="border-b border-line">
              {hg.headers.map((h) => {
                const sort = h.column.getIsSorted();
                const canSort = h.column.getCanSort();
                const label = flexRender(h.column.columnDef.header, h.getContext());
                const indicator = sort === "asc" ? " ▲" : sort === "desc" ? " ▼" : "";
                return (
                  <th
                    key={h.id}
                    scope="col"
                    aria-sort={
                      !canSort ? undefined : sort === "asc" ? "ascending" : sort === "desc" ? "descending" : "none"
                    }
                    className={`select-none whitespace-nowrap px-2 py-2 text-left text-[11px] font-medium uppercase tracking-wide text-muted sm:px-3 ${
                      columnClassName?.(h.column.id) ?? ""
                    }`}
                  >
                    {canSort ? (
                      // Real <button> so headers are focusable + toggle on Enter/Space
                      // (keyboard a11y); focus ring comes from the global :focus-visible.
                      <button
                        type="button"
                        onClick={h.column.getToggleSortingHandler()}
                        className="-mx-1 flex items-center gap-1 rounded px-1 uppercase tracking-wide hover:text-secondary"
                      >
                        {label}
                        {indicator}
                      </button>
                    ) : (
                      label
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr
              key={row.id}
              className={`border-b border-line/60 hover:bg-rowhover ${
                rowClassName ? rowClassName(row.original) : ""
              }`}
            >
              {row.getVisibleCells().map((cell) => (
                <td
                  key={cell.id}
                  className={`px-2 py-2 align-middle sm:px-3 ${
                    columnClassName?.(cell.column.id) ?? ""
                  }`}
                >
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
