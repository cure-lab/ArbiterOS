"use client";

import { Check, ChevronDown, ChevronRight, Languages } from "lucide-react";
import { Button } from "@/src/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/src/components/ui/dropdown-menu";
import { SidebarMenuButton } from "@/src/components/ui/sidebar";
import { cn } from "@/src/utils/tailwind";
import { useLanguage } from "./LanguageProvider";
import { APP_LANGUAGES, type AppLanguage } from "./constants";

type LanguageSwitcherProps = {
  variant: "auth" | "sidebar";
  className?: string;
};

function LanguageLabel({
  language,
  variant,
}: {
  language: AppLanguage;
  variant: LanguageSwitcherProps["variant"];
}) {
  const { t } = useLanguage();

  return (
    <>
      <Languages className="h-4 w-4" />
      <span>
        {language === "en"
          ? t("common.english")
          : t("common.simplifiedChinese")}
      </span>
      {variant === "sidebar" ? (
        <ChevronRight className="ml-auto h-4 w-4 opacity-60" />
      ) : (
        <ChevronDown className="ml-auto h-4 w-4 opacity-60" />
      )}
    </>
  );
}

export function LanguageSwitcher({
  variant,
  className,
}: LanguageSwitcherProps) {
  const { language, setLanguage, t } = useLanguage();

  const trigger =
    variant === "sidebar" ? (
      <SidebarMenuButton
        tooltip={t("common.language")}
        className={cn("w-full", className)}
      >
        <LanguageLabel language={language} variant={variant} />
      </SidebarMenuButton>
    ) : (
      <Button variant="outline" size="sm" className={className}>
        <LanguageLabel language={language} variant={variant} />
      </Button>
    );

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>{trigger}</DropdownMenuTrigger>
      <DropdownMenuContent
        side={variant === "sidebar" ? "right" : "bottom"}
        align={variant === "sidebar" ? "start" : "start"}
        sideOffset={8}
      >
        {APP_LANGUAGES.map((option) => {
          const label =
            option === "en"
              ? t("common.english")
              : t("common.simplifiedChinese");

          return (
            <DropdownMenuItem
              key={option}
              onClick={() => setLanguage(option)}
              className="flex items-center gap-2"
            >
              <span className="flex-1">{label}</span>
              {language === option ? <Check className="h-4 w-4" /> : null}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
