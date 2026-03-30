import Header from "@/src/components/layouts/header";
import React from "react";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export const SettingsDangerZone: React.FC<{
  items: {
    title: string;
    description: string;
    button: React.ReactNode;
  }[];
}> = ({ items }) => {
  const { language } = useLanguage();

  return (
    <div className="space-y-3">
      <Header title={localize(language, "Danger Zone", "危险区域")} />
      <div className="rounded-lg border">
        {items.map((item, index) => (
          <div
            key={index}
            className="flex items-center justify-between gap-4 border-b p-3 last:border-b-0"
          >
            <div>
              <h4 className="font-semibold">{item.title}</h4>
              <p className="text-sm">{item.description}</p>
            </div>
            {item.button}
          </div>
        ))}
      </div>
    </div>
  );
};
