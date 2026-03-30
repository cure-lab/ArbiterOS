import React from "react";
import Header from "@/src/components/layouts/header";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import { ScoreConfigsTable } from "@/src/components/table/use-cases/score-configs";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export function ScoreConfigSettings({ projectId }: { projectId: string }) {
  const { language } = useLanguage();
  const hasReadAccess = useHasProjectAccess({
    projectId: projectId,
    scope: "scoreConfigs:read",
  });

  if (!hasReadAccess) return null;

  return (
    <div id="score-configs">
      <Header title={localize(language, "Score Configs", "评分配置")} />
      <p className="mb-2 text-sm">
        {localize(
          language,
          "Score configs define which scores are available for",
          "评分配置定义了项目中可用于",
        )}{" "}
        <a
          href="https://langfuse.com/docs/evaluation/evaluation-methods/annotation"
          className="underline"
          target="_blank"
          rel="noopener noreferrer"
        >
          {localize(language, "annotation", "标注")}
        </a>{" "}
        {localize(
          language,
          "in your project. Please note that all score configs are immutable.",
          "的分数。请注意，所有评分配置都是不可变的。",
        )}
      </p>
      <ScoreConfigsTable projectId={projectId} />
    </div>
  );
}
