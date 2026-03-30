import Header from "@/src/components/layouts/header";
import ModelTable from "@/src/components/table/use-cases/models";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export function ModelsSettings(props: { projectId: string }) {
  const { language } = useLanguage();

  return (
    <>
      <Header title={localize(language, "Model Definitions", "模型定义")} />
      <p className="mb-2 text-sm">
        {localize(
          language,
          "A configuration that stores pricing information for an LLM model. Model definitions specify the cost per input and output token, enabling Langfuse to automatically calculate the price of generations based on token usage.",
          "用于存储 LLM 模型定价信息的配置。模型定义指定每个输入和输出词元的成本，使 Langfuse 能够基于词元使用量自动计算生成价格。",
        )}
      </p>
      <ModelTable projectId={props.projectId} />
    </>
  );
}
