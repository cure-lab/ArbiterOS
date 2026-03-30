import { RefreshCw, ChevronDown } from "lucide-react";
import { Button } from "@/src/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/src/components/ui/dropdown-menu";
import { cn } from "@/src/utils/tailwind";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export const REFRESH_INTERVALS = [
  { label: "Off", value: null },
  { label: "30s", value: 30_000 },
  { label: "1m", value: 60_000 },
  { label: "5m", value: 300_000 },
  { label: "15m", value: 900_000 },
] as const;

export type RefreshInterval = (typeof REFRESH_INTERVALS)[number]["value"];

interface DataTableRefreshButtonProps {
  onRefresh: () => void;
  isRefreshing: boolean;
  interval: RefreshInterval;
  setInterval: (interval: RefreshInterval) => void;
}

export function DataTableRefreshButton({
  onRefresh,
  isRefreshing,
  interval,
  setInterval,
}: DataTableRefreshButtonProps) {
  const { language } = useLanguage();
  const activeInterval = REFRESH_INTERVALS.find((i) => i.value === interval);
  const formatIntervalLabel = (label: string) =>
    label === "Off" ? localize(language, "Off", "关闭") : label;

  return (
    <div className="flex items-center">
      <Button
        variant="outline"
        size="icon"
        onClick={onRefresh}
        disabled={isRefreshing}
        className="rounded-r-none border-r-0"
        title={localize(language, "Refresh", "刷新")}
      >
        <RefreshCw className={cn("h-4 w-4", isRefreshing && "animate-spin")} />
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            size="icon"
            className="w-auto rounded-l-none border-l-0 px-2"
          >
            <ChevronDown className="h-4 w-4" />
            <span className="ml-1 text-sm">
              {activeInterval
                ? formatIntervalLabel(activeInterval.label)
                : localize(language, "Off", "关闭")}
            </span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuRadioGroup
            value={String(interval)}
            onValueChange={(value) =>
              setInterval(
                value === "null" ? null : (Number(value) as RefreshInterval),
              )
            }
          >
            {REFRESH_INTERVALS.map((option) => (
              <DropdownMenuRadioItem
                key={String(option.value)}
                value={String(option.value)}
              >
                {option.label === "Off"
                  ? localize(language, "Auto-refresh off", "关闭自动刷新")
                  : localize(
                      language,
                      `Every ${option.label}`,
                      `每 ${option.label}`,
                    )}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
