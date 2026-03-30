import { type MultiSelect } from "@/src/components/table/data-table-toolbar";
import { Button } from "@/src/components/ui/button";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export function DataTableSelectAllBanner({
  selectAll,
  setSelectAll,
  setRowSelection,
  pageSize,
  totalCount,
}: MultiSelect) {
  const { language } = useLanguage();
  return (
    <div className="mb-2 flex flex-wrap items-center justify-center gap-2 rounded-sm bg-input p-2 @container">
      {selectAll ? (
        <span className="text-sm">
          {localize(
            language,
            `All ${totalCount ?? 0} items are selected.`,
            `已选中全部 ${totalCount ?? 0} 项。`,
          )}{" "}
          <Button
            variant="ghost"
            className="h-auto p-0 font-semibold text-accent-dark-blue hover:text-accent-dark-blue/80"
            onClick={() => {
              setSelectAll(false);
              setRowSelection({});
            }}
          >
            {localize(language, "Clear selection", "清除选择")}
          </Button>
        </span>
      ) : (
        <span className="text-sm">
          {localize(
            language,
            `All ${pageSize} items on this page are selected.`,
            `当前页的 ${pageSize} 项已全部选中。`,
          )}{" "}
          <Button
            variant="ghost"
            className="h-auto p-0 font-semibold text-accent-dark-blue hover:text-accent-dark-blue/80"
            onClick={() => {
              setSelectAll(true);
            }}
          >
            {localize(
              language,
              `Select all ${totalCount ?? 0} items`,
              `选择全部 ${totalCount ?? 0} 项`,
            )}
          </Button>
        </span>
      )}
    </div>
  );
}
