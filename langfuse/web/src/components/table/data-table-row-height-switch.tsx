import { Button } from "@/src/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
  DropdownMenuPortal,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuCheckboxItem,
} from "@/src/components/ui/dropdown-menu";
import useLocalStorage from "@/src/components/useLocalStorage";
import { usePostHogClientCapture } from "@/src/features/posthog-analytics/usePostHogClientCapture";
import { Rows3, Rows2, Rows4 } from "lucide-react";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

const heightOptions = [
  { id: "s", label: "Small", icon: <Rows4 /> },
  { id: "m", label: "Medium", icon: <Rows3 /> },
  { id: "l", label: "Large", icon: <Rows2 /> },
] as const;

const defaultHeights: Record<RowHeight, string> = {
  s: "h-7", // after removing the container around IO, we want the row height a bit more than 6
  m: "h-24",
  l: "h-64",
};

export type RowHeight = (typeof heightOptions)[number]["id"];
export type CustomHeights = Record<RowHeight, string>;

export const getRowHeightTailwindClass = (
  rowHeight?: RowHeight,
  customHeights?: CustomHeights,
) => {
  if (!rowHeight) return undefined;
  return customHeights?.[rowHeight] || defaultHeights[rowHeight];
};

export function useRowHeightLocalStorage(
  tableName: string,
  defaultValue: RowHeight,
) {
  const [rowHeight, setRowHeight, clearRowHeight] = useLocalStorage<RowHeight>(
    `${tableName}Height`,
    defaultValue,
  );

  return [rowHeight, setRowHeight, clearRowHeight] as const;
}

export const DataTableRowHeightSwitch = ({
  rowHeight,
  setRowHeight,
}: {
  rowHeight: RowHeight;
  setRowHeight: (e: RowHeight) => void;
}) => {
  const capture = usePostHogClientCapture();
  const { language } = useLanguage();
  const getHeightLabel = (id: RowHeight) =>
    id === "s"
      ? localize(language, "Small", "小")
      : id === "m"
        ? localize(language, "Medium", "中")
        : localize(language, "Large", "大");
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="icon"
          title={localize(language, "Row height", "行高")}
        >
          <Rows3 className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuPortal>
        <DropdownMenuContent>
          <DropdownMenuLabel>
            {localize(language, "Row height", "行高")}
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          {heightOptions.map(({ id }) => (
            <DropdownMenuCheckboxItem
              key={id}
              checked={rowHeight === id}
              onClick={(e) => {
                // Prevent closing the dropdown menu to allow the user to adjust their selection
                e.preventDefault();
                capture("table:row_height_switch_select", {
                  rowHeight: id,
                });
                setRowHeight(id);
              }}
            >
              {getHeightLabel(id)}
            </DropdownMenuCheckboxItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenuPortal>
    </DropdownMenu>
  );
};
