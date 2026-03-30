import { Prisma, type PrismaClient } from "@prisma/client";
import type { FilterState } from "@langfuse/shared";
import {
  UNCLASSIFIED_ERROR_TYPE_FILTER_LABEL,
  UNCLASSIFIED_ERROR_TYPE_FILTER_LABEL_EN,
  UNCLASSIFIED_ERROR_TYPE_FILTER_VALUE,
} from "../types";

function isErrorTypeColumn(column: unknown): boolean {
  const c = String(column ?? "")
    .trim()
    .toLowerCase();
  return c === "errortype" || c === "error type";
}

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((v) => String(v).trim()).filter((v) => v.length > 0);
}

function isUnclassifiedErrorTypeValue(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  return (
    normalized === UNCLASSIFIED_ERROR_TYPE_FILTER_VALUE.toLowerCase() ||
    normalized === UNCLASSIFIED_ERROR_TYPE_FILTER_LABEL.toLowerCase() ||
    normalized === UNCLASSIFIED_ERROR_TYPE_FILTER_LABEL_EN.toLowerCase() ||
    normalized === "unclassified"
  );
}

function parseErrorTypeFilter(filter: any): {
  mode: "include" | "exclude" | "unsupported";
  values: string[];
} {
  if (!filter || !isErrorTypeColumn(filter.column)) {
    return { mode: "unsupported", values: [] };
  }

  const type = String(filter.type ?? "").toLowerCase();
  const operator = String(filter.operator ?? "").toLowerCase();

  // Checkbox-based facet selections
  if (type === "stringoptions" && operator === "any of") {
    return { mode: "include", values: normalizeStringList(filter.value) };
  }
  if (type === "stringoptions" && operator === "none of") {
    return { mode: "exclude", values: normalizeStringList(filter.value) };
  }

  // Freeform filter builder usage
  if (type === "string" && operator === "=") {
    const v = filter.value != null ? String(filter.value).trim() : "";
    return { mode: "include", values: v ? [v] : [] };
  }

  // Unsupported operators/types: strip to avoid breaking ClickHouse mappings.
  return { mode: "unsupported", values: [] };
}

async function getObservationIdsFromErrorAnalyses(params: {
  prisma: PrismaClient;
  projectId: string;
  whereSql: Prisma.Sql;
}): Promise<string[] | null> {
  try {
    const rows = await params.prisma.$queryRaw<
      Array<{ observationId: string }>
    >(
      Prisma.sql`
        SELECT DISTINCT ea.observation_id AS "observationId"
        FROM error_analyses ea
        WHERE ea.project_id = ${params.projectId}
          AND (${params.whereSql})
      `,
    );
    return rows.map((r) => r.observationId).filter((id) => Boolean(id));
  } catch {
    // If the DB/client doesn't support this table/column yet, don't block the query.
    return null;
  }
}

export async function applyErrorTypeFilters(params: {
  prisma: PrismaClient;
  projectId: string;
  filterState: FilterState;
}): Promise<{ filterState: FilterState; hasNoMatches: boolean }> {
  const selectedTypes = new Set<string>();
  const excludedTypes = new Set<string>();
  let sawErrorTypeFilter = false;
  const remaining: FilterState = [];

  for (const f of params.filterState ?? []) {
    if (!isErrorTypeColumn((f as any)?.column)) {
      remaining.push(f);
      continue;
    }

    sawErrorTypeFilter = true;
    const parsed = parseErrorTypeFilter(f);
    if (parsed.mode === "include") {
      parsed.values.forEach((v) => selectedTypes.add(v));
    } else if (parsed.mode === "exclude") {
      parsed.values.forEach((v) => excludedTypes.add(v));
    }
    // Always strip errorType filters here. We'll translate them into an ID filter below.
  }

  if (!sawErrorTypeFilter) {
    return { filterState: params.filterState, hasNoMatches: false };
  }

  const includeUnclassified = [...selectedTypes].some(
    isUnclassifiedErrorTypeValue,
  );
  const excludeUnclassified = [...excludedTypes].some(
    isUnclassifiedErrorTypeValue,
  );
  const selectedClassifiedTypes = [...selectedTypes].filter(
    (v) => !isUnclassifiedErrorTypeValue(v),
  );
  const excludedClassifiedTypes = [...excludedTypes].filter(
    (v) => !isUnclassifiedErrorTypeValue(v),
  );

  // If the filter was present but had no concrete values (or unsupported operator),
  // treat it as a no-op and keep the rest of the filters.
  if (
    selectedClassifiedTypes.length === 0 &&
    excludedClassifiedTypes.length === 0 &&
    !includeUnclassified &&
    !excludeUnclassified
  ) {
    return { filterState: remaining, hasNoMatches: false };
  }

  let predicate: Prisma.Sql | null = null;

  if (selectedClassifiedTypes.length > 0 || includeUnclassified) {
    if (selectedClassifiedTypes.length > 0 && includeUnclassified) {
      predicate = Prisma.sql`(ea.error_type IN (${Prisma.join(selectedClassifiedTypes)}) OR ea.error_type IS NULL)`;
    } else if (selectedClassifiedTypes.length > 0) {
      predicate = Prisma.sql`ea.error_type IN (${Prisma.join(selectedClassifiedTypes)})`;
    } else {
      predicate = Prisma.sql`ea.error_type IS NULL`;
    }
  } else {
    // Exclude mode with support for "unclassified"
    if (excludedClassifiedTypes.length > 0) {
      predicate = excludeUnclassified
        ? Prisma.sql`ea.error_type IS NOT NULL AND ea.error_type NOT IN (${Prisma.join(excludedClassifiedTypes)})`
        : Prisma.sql`(ea.error_type IS NULL OR ea.error_type NOT IN (${Prisma.join(excludedClassifiedTypes)}))`;
    } else if (excludeUnclassified) {
      predicate = Prisma.sql`ea.error_type IS NOT NULL`;
    }
  }

  if (!predicate) {
    return { filterState: remaining, hasNoMatches: false };
  }

  // NOTE: Observations can be served from ClickHouse "events table" mode where
  // not all observations exist in Postgres. Therefore, we avoid querying the
  // Postgres `observations` table here and instead translate errorType filters
  // into ID filters based solely on `error_analyses`.
  //
  // For "unclassified" we interpret it as: observations without a non-null
  // `error_type` classification (includes: no analysis row, or analysis row with null error_type).
  //
  // Since we cannot enumerate "no analysis row" IDs from Postgres, we express
  // unclassified (and unions involving it) via exclusion of classified IDs.

  // INCLUDE MODE:
  // - classified only: id IN analyzed IDs for selected types
  // - unclassified only: id NOT IN IDs with error_type IS NOT NULL
  // - unclassified + classified: id NOT IN IDs with error_type NOT IN selected types
  //
  // EXCLUDE MODE:
  // - exclude classified types: id NOT IN IDs with error_type IN excluded types
  // - exclude unclassified: id IN IDs with error_type IS NOT NULL
  // - exclude unclassified + classified: id IN IDs with error_type IS NOT NULL AND error_type NOT IN excluded types

  const mode =
    selectedClassifiedTypes.length > 0 || includeUnclassified
      ? "include"
      : "exclude";

  if (mode === "include") {
    // If includeUnclassified is present, we implement a "NOT IN" filter.
    if (includeUnclassified) {
      const excludedIdsResult =
        selectedClassifiedTypes.length > 0
          ? await getObservationIdsFromErrorAnalyses({
              prisma: params.prisma,
              projectId: params.projectId,
              whereSql:
                selectedClassifiedTypes.length === 0
                  ? Prisma.sql`ea.error_type IS NOT NULL`
                  : Prisma.sql`ea.error_type IS NOT NULL AND ea.error_type NOT IN (${Prisma.join(selectedClassifiedTypes)})`,
            })
          : await getObservationIdsFromErrorAnalyses({
              prisma: params.prisma,
              projectId: params.projectId,
              whereSql: Prisma.sql`ea.error_type IS NOT NULL`,
            });

      if (excludedIdsResult === null) {
        return { filterState: remaining, hasNoMatches: false };
      }

      const next: FilterState = [
        ...remaining,
        {
          column: "id",
          type: "stringOptions",
          operator: "none of",
          value: excludedIdsResult,
        } as any,
      ];

      return { filterState: next, hasNoMatches: false };
    }

    const includedIdsResult = await getObservationIdsFromErrorAnalyses({
      prisma: params.prisma,
      projectId: params.projectId,
      whereSql: Prisma.sql`ea.error_type IN (${Prisma.join(selectedClassifiedTypes)})`,
    });
    if (includedIdsResult === null) {
      return { filterState: remaining, hasNoMatches: false };
    }
    if (includedIdsResult.length === 0) {
      return { filterState: remaining, hasNoMatches: true };
    }

    return {
      filterState: [
        ...remaining,
        {
          column: "id",
          type: "stringOptions",
          operator: "any of",
          value: includedIdsResult,
        } as any,
      ],
      hasNoMatches: false,
    };
  }

  // EXCLUDE MODE
  if (excludeUnclassified && excludedClassifiedTypes.length === 0) {
    const classifiedIdsResult = await getObservationIdsFromErrorAnalyses({
      prisma: params.prisma,
      projectId: params.projectId,
      whereSql: Prisma.sql`ea.error_type IS NOT NULL`,
    });
    if (classifiedIdsResult === null) {
      return { filterState: remaining, hasNoMatches: false };
    }
    if (classifiedIdsResult.length === 0) {
      return { filterState: remaining, hasNoMatches: true };
    }
    return {
      filterState: [
        ...remaining,
        {
          column: "id",
          type: "stringOptions",
          operator: "any of",
          value: classifiedIdsResult,
        } as any,
      ],
      hasNoMatches: false,
    };
  }

  if (excludeUnclassified && excludedClassifiedTypes.length > 0) {
    const allowedIdsResult = await getObservationIdsFromErrorAnalyses({
      prisma: params.prisma,
      projectId: params.projectId,
      whereSql: Prisma.sql`ea.error_type IS NOT NULL AND ea.error_type NOT IN (${Prisma.join(excludedClassifiedTypes)})`,
    });
    if (allowedIdsResult === null) {
      return { filterState: remaining, hasNoMatches: false };
    }
    if (allowedIdsResult.length === 0) {
      return { filterState: remaining, hasNoMatches: true };
    }
    return {
      filterState: [
        ...remaining,
        {
          column: "id",
          type: "stringOptions",
          operator: "any of",
          value: allowedIdsResult,
        } as any,
      ],
      hasNoMatches: false,
    };
  }

  // exclude classified types only
  if (excludedClassifiedTypes.length > 0) {
    const excludedIdsResult = await getObservationIdsFromErrorAnalyses({
      prisma: params.prisma,
      projectId: params.projectId,
      whereSql: Prisma.sql`ea.error_type IN (${Prisma.join(excludedClassifiedTypes)})`,
    });
    if (excludedIdsResult === null) {
      return { filterState: remaining, hasNoMatches: false };
    }
    const next: FilterState = [
      ...remaining,
      {
        column: "id",
        type: "stringOptions",
        operator: "none of",
        value: excludedIdsResult,
      } as any,
    ];
    return { filterState: next, hasNoMatches: false };
  }

  // Fallback: nothing to do
  return { filterState: remaining, hasNoMatches: false };
}

export async function getErrorTypeFilterOptions(params: {
  prisma: PrismaClient;
  projectId: string;
}): Promise<Array<{ value: string; count?: number; displayValue?: string }>> {
  let grouped: Array<any> = [];
  try {
    const delegate = (params.prisma as any).errorAnalysis as any;
    grouped = (await delegate.groupBy({
      by: ["errorType"],
      where: {
        projectId: params.projectId,
        errorType: { not: null },
      },
      _count: {
        errorType: true,
      },
    })) as Array<any>;
  } catch {
    // If the DB/client doesn't support errorType yet, omit facet options.
    return [];
  }

  const classifiedMap = new Map<string, number>();
  for (const g of grouped) {
    const raw = g?.errorType != null ? String(g.errorType).trim() : "";
    if (!raw || isUnclassifiedErrorTypeValue(raw)) continue;
    const count = Number(g?._count?.errorType ?? g?._count?._all ?? 0);
    classifiedMap.set(raw, (classifiedMap.get(raw) ?? 0) + count);
  }

  const classified = Array.from(classifiedMap.entries()).map(
    ([value, count]) => ({
      value,
      count,
    }),
  );

  // Always include an "unclassified" option.
  // We intentionally omit a count here because in events-table mode, not all observations
  // necessarily exist in Postgres, making a complete count expensive/unreliable.
  const withUnclassified = [
    ...classified,
    {
      value: UNCLASSIFIED_ERROR_TYPE_FILTER_VALUE,
      displayValue: UNCLASSIFIED_ERROR_TYPE_FILTER_LABEL,
    },
  ];

  return withUnclassified.sort(
    (a, b) =>
      ("count" in b ? (b.count ?? 0) : 0) - ("count" in a ? (a.count ?? 0) : 0),
  );
}
