import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/src/components/ui/card";
import { AlertCircle } from "lucide-react";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

type NoMatchDisplayProps = {
  modelName: string;
};

export type { NoMatchDisplayProps };

export function NoMatchDisplay({ modelName }: NoMatchDisplayProps) {
  const { language } = useLanguage();

  return (
    <Card className="border-destructive/50 bg-destructive/5">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base text-destructive">
          <AlertCircle className="h-5 w-5" />
          {localize(language, "No Match Found", "未找到匹配")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm">
          {localize(
            language,
            `No model configuration matches "${modelName}" in this project.`,
            `此项目中没有与“${modelName}”匹配的模型配置。`,
          )}
        </p>

        <div>
          <p className="mb-2 text-sm font-medium">
            {localize(language, "Suggestions:", "建议：")}
          </p>
          <ul className="list-inside list-disc space-y-1 text-sm text-muted-foreground">
            <li>
              {localize(
                language,
                "Check your model name spelling",
                "检查模型名称拼写",
              )}
            </li>
            <li>
              {localize(
                language,
                "View existing models and their match patterns",
                "查看现有模型及其匹配模式",
              )}
            </li>
            <li>
              {localize(
                language,
                "Create a new model definition",
                "创建新的模型定义",
              )}
            </li>
          </ul>
        </div>
      </CardContent>
    </Card>
  );
}
