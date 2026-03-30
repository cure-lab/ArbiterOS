import React from "react";
import { Button } from "@/src/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/src/components/ui/dialog";
import DiffViewer from "@/src/components/DiffViewer";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

type CorrectedOutputDiffDialogProps = {
  isOpen: boolean;
  setIsOpen: (open: boolean) => void;
  actualOutput?: unknown;
  correctedOutput: string;
  strictJsonMode: boolean;
};

/**
 * Formats output for diff display
 * @param output - The output to format
 * @param strictJsonMode - Whether to enforce JSON formatting
 * @returns Formatted string for display
 */
const formatOutputForDiff = (
  output: unknown,
  strictJsonMode: boolean,
): string => {
  if (output === null || output === undefined) {
    return "";
  }

  // If strict JSON mode, try to format as JSON
  if (strictJsonMode) {
    try {
      // If it's already a string, try to parse it first
      if (typeof output === "string") {
        const parsed = JSON.parse(output);
        return JSON.stringify(parsed, null, 2);
      }
      // Otherwise just stringify the object
      return JSON.stringify(output, null, 2);
    } catch {
      // If JSON formatting fails, fall back to string representation
      return typeof output === "string" ? output : JSON.stringify(output);
    }
  }

  // Non-strict mode: convert to string
  return typeof output === "string" ? output : JSON.stringify(output, null, 2);
};

export const CorrectedOutputDiffDialog: React.FC<
  CorrectedOutputDiffDialogProps
> = ({ isOpen, setIsOpen, actualOutput, correctedOutput, strictJsonMode }) => {
  const { language } = useLanguage();

  // Format both outputs for comparison
  const formattedActualOutput = formatOutputForDiff(
    actualOutput,
    strictJsonMode,
  );
  const formattedCorrectedOutput = formatOutputForDiff(
    correctedOutput,
    strictJsonMode,
  );

  // Check if there's no original output to compare
  const hasNoOriginalOutput =
    actualOutput === null || actualOutput === undefined;

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogContent size="xl">
        <DialogHeader>
          <DialogTitle>
            {localize(language, "Output Correction Diff", "输出修正对比")}
          </DialogTitle>
          <DialogDescription>
            {localize(
              language,
              "Compare the original output with the corrected version",
              "对比原始输出与修正后的版本",
            )}
          </DialogDescription>
        </DialogHeader>

        <DialogBody>
          {hasNoOriginalOutput ? (
            <div className="flex flex-col items-center justify-center p-8 text-center">
              <div className="text-muted-foreground">
                <p className="text-lg font-medium">
                  {localize(language, "No original output", "没有原始输出")}
                </p>
                <p className="mt-2 text-sm">
                  {localize(
                    language,
                    "There is no original output to compare with the correction.",
                    "没有可与修正结果进行比较的原始输出。",
                  )}
                </p>
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              <DiffViewer
                oldString={formattedActualOutput}
                newString={formattedCorrectedOutput}
                oldLabel={localize(language, "Original Output", "原始输出")}
                newLabel={localize(language, "Corrected Output", "修正输出")}
              />
            </div>
          )}
        </DialogBody>

        <DialogFooter>
          <Button onClick={() => setIsOpen(false)}>
            {localize(language, "Close", "关闭")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
