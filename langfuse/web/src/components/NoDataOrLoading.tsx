import React from "react";
import { cn } from "@/src/utils/tailwind";
import DocPopup from "@/src/components/layouts/doc-popup";
import { Skeleton } from "@/src/components/ui/skeleton";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

interface NoDataOrLoadingProps {
  isLoading: boolean;
  description?: string;
  href?: string;
  className?: string;
}
interface NoDataProps {
  noDataText?: string;
  children?: React.ReactNode;
  className?: string;
}

const NoData = ({ noDataText, children, className }: NoDataProps) => {
  const { language } = useLanguage();

  return (
    <div
      className={cn(
        "flex h-3/4 min-h-[9rem] w-full items-center justify-center rounded-md border border-dashed",
        className,
      )}
    >
      <p className="text-muted-foreground">
        {noDataText ?? localize(language, "No data", "没有数据")}
      </p>
      {children}
    </div>
  );
};

export function NoDataOrLoading({
  isLoading,
  description,
  href,
  className,
}: NoDataOrLoadingProps) {
  if (isLoading) {
    return (
      <div
        className={cn(
          "flex h-3/4 min-h-[9rem] w-full items-center justify-center rounded-md",
          className,
        )}
      >
        <Skeleton className="h-full w-full" />
      </div>
    );
  }

  return (
    <NoData className={className}>
      {description && <DocPopup description={description} href={href} />}
    </NoData>
  );
}
