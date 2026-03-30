"use client";

import { Button } from "@/src/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/src/components/ui/popover";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";
import { ErrorAnalysisDropdown } from "./ErrorAnalysisDropdown";

export function ErrorAnalysisButton(props: {
  projectId: string;
  traceId: string;
  observationId: string;
  level: string | null | undefined;
}) {
  const { language } = useLanguage();

  if (props.level !== "ERROR" && props.level !== "WARNING") return null;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="xs"
          className="h-6 border-orange-200 bg-orange-50 px-2 text-xs font-medium text-orange-700 shadow-sm transition-all hover:-translate-y-px hover:border-orange-300 hover:bg-orange-100 hover:text-orange-800 hover:shadow-md focus-visible:ring-orange-300 dark:border-orange-900/60 dark:bg-orange-950/40 dark:text-orange-300 dark:hover:border-orange-800 dark:hover:bg-orange-950/70 dark:hover:text-orange-200"
        >
          {localize(language, "Analyze", "分析")}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="p-3">
        <ErrorAnalysisDropdown
          projectId={props.projectId}
          traceId={props.traceId}
          observationId={props.observationId}
          level={props.level}
        />
      </PopoverContent>
    </Popover>
  );
}
