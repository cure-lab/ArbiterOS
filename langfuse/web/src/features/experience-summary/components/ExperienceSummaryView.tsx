"use client";

import { useCallback, useState } from "react";
import { Check, ChevronDown, Copy } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/src/components/ui/badge";
import { Button } from "@/src/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/src/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
} from "@/src/components/ui/collapsible";
import { copyTextToClipboard } from "@/src/utils/clipboard";
import { cn } from "@/src/utils/tailwind";
import type { ExperienceSummaryJson } from "../types";

function getExperienceAnchorId(key: string) {
  return `experience-pack-${key}`;
}

const PROMPT_PACK_PREVIEW_LINE_COUNT = 6;

export function ExperienceSummaryView(props: {
  summary: ExperienceSummaryJson;
  onRequestEdit?: (params: {
    section: "prompt_pack" | "experience";
    experienceKey?: string;
  }) => void;
  expandedExperienceKeys?: string[];
  onExpandedExperienceKeysChange?: (keys: string[]) => void;
}) {
  const {
    summary,
    onRequestEdit,
    expandedExperienceKeys,
    onExpandedExperienceKeysChange,
  } = props;
  const [copiedPromptKey, setCopiedPromptKey] = useState<string | null>(null);
  const [promptPackExpanded, setPromptPackExpanded] = useState(false);
  const [internalExpandedExperienceKeys, setInternalExpandedExperienceKeys] =
    useState<string[]>([]);

  const handleCopyPromptAdditions = useCallback(
    async (params: { key: string; text: string }) => {
      try {
        await copyTextToClipboard(params.text);
        setCopiedPromptKey(params.key);
        toast.success("Copied prompt additions");
        window.setTimeout(() => {
          setCopiedPromptKey((current) =>
            current === params.key ? null : current,
          );
        }, 1500);
      } catch (error) {
        console.error("Failed to copy prompt additions", error);
        toast.error("Failed to copy prompt additions");
      }
    },
    [],
  );

  const promptPackLines = summary.promptPack.lines ?? [];
  const isPromptPackOpen = promptPackExpanded;
  const currentExpandedExperienceKeys =
    expandedExperienceKeys ?? internalExpandedExperienceKeys;
  const promptPackLinesToDisplay = isPromptPackOpen
    ? promptPackLines
    : promptPackLines.slice(0, PROMPT_PACK_PREVIEW_LINE_COUNT);
  const promptPackHiddenLineCount =
    promptPackLines.length - promptPackLinesToDisplay.length;

  const setExperienceExpanded = useCallback(
    (params: { key: string; open: boolean }) => {
      const currentKeys =
        expandedExperienceKeys ?? internalExpandedExperienceKeys;
      const hasKey = currentKeys.includes(params.key);
      const nextKeys = params.open
        ? hasKey
          ? currentKeys
          : [...currentKeys, params.key]
        : currentKeys.filter((value) => value !== params.key);
      if (onExpandedExperienceKeysChange) {
        onExpandedExperienceKeysChange(nextKeys);
      } else {
        setInternalExpandedExperienceKeys(nextKeys);
      }
    },
    [
      expandedExperienceKeys,
      internalExpandedExperienceKeys,
      onExpandedExperienceKeysChange,
    ],
  );

  return (
    <div className="flex flex-col gap-3">
      <Card onClick={() => onRequestEdit?.({ section: "prompt_pack" })}>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle className="text-base">Prompt pack</CardTitle>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={(e) => {
                e.stopPropagation();
                setPromptPackExpanded((current) => !current);
              }}
            >
              {isPromptPackOpen ? "Collapse" : "Expand"}
              <ChevronDown
                className={cn(
                  "ml-1 h-3.5 w-3.5 transition-transform",
                  isPromptPackOpen ? "rotate-180" : "rotate-0",
                )}
              />
            </Button>
          </div>
          <CardDescription>
            Paste these lines into your prompt to reduce recurring errors.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Collapsible
            open={isPromptPackOpen}
            onOpenChange={setPromptPackExpanded}
          >
            <div className="group relative">
              <Button
                variant="ghost"
                size="icon"
                className="absolute right-1 top-1 z-10 h-6 w-6 opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
                onClick={(e) => {
                  e.stopPropagation();
                  void handleCopyPromptAdditions({
                    key: "__prompt_pack__",
                    text: promptPackLines.join("\n"),
                  });
                }}
                aria-label="Copy prompt pack"
              >
                {copiedPromptKey === "__prompt_pack__" ? (
                  <Check className="h-3.5 w-3.5" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
              </Button>
              <div className="whitespace-pre-wrap rounded-md border bg-background p-2 pr-10 font-mono text-xs text-muted-foreground">
                {promptPackLinesToDisplay.join("\n")}
              </div>
            </div>
          </Collapsible>
          {!isPromptPackOpen && promptPackHiddenLineCount > 0 ? (
            <div className="mt-2 text-xs text-muted-foreground">
              +{promptPackHiddenLineCount} more lines
            </div>
          ) : null}
        </CardContent>
      </Card>

      {summary.experiences.map((exp) => {
        const isExpanded = currentExpandedExperienceKeys.includes(exp.key);

        return (
          <Card
            key={exp.key}
            id={getExperienceAnchorId(exp.key)}
            className="scroll-mt-20"
            onClick={() =>
              onRequestEdit?.({
                section: "experience",
                experienceKey: exp.key,
              })
            }
          >
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <CardTitle className="text-base">{exp.key}</CardTitle>
                  {exp.relatedErrorTypes?.length ? (
                    <div className="flex flex-wrap items-center gap-1">
                      {exp.relatedErrorTypes.slice(0, 6).map((t, idx) => (
                        <Badge
                          key={`${t}-${idx}`}
                          variant="outline"
                          className="border-red-200 bg-red-100 font-mono text-red-700 dark:border-red-900 dark:bg-red-900/40 dark:text-red-300"
                        >
                          {t}
                        </Badge>
                      ))}
                      {exp.relatedErrorTypes.length > 6 ? (
                        <Badge
                          variant="outline"
                          className="border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-900/20 dark:text-red-300"
                        >
                          +{exp.relatedErrorTypes.length - 6}
                        </Badge>
                      ) : null}
                    </div>
                  ) : null}
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={(e) => {
                    e.stopPropagation();
                    setExperienceExpanded({ key: exp.key, open: !isExpanded });
                  }}
                >
                  {isExpanded ? "Collapse" : "Expand"}
                  <ChevronDown
                    className={cn(
                      "ml-1 h-3.5 w-3.5 transition-transform",
                      isExpanded ? "rotate-180" : "rotate-0",
                    )}
                  />
                </Button>
              </div>
              <CardDescription
                className={cn(
                  "whitespace-pre-wrap break-words",
                  !isExpanded && "line-clamp-2",
                )}
              >
                {exp.when}
              </CardDescription>
            </CardHeader>
            <Collapsible
              open={isExpanded}
              onOpenChange={(open) =>
                setExperienceExpanded({ key: exp.key, open })
              }
            >
              <CollapsibleContent>
                <CardContent className="text-sm">
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                    <div>
                      <div className="text-xs font-medium text-muted-foreground">
                        Possible problems
                      </div>
                      <ul className="mt-2 list-disc space-y-1 pl-5">
                        {exp.possibleProblems.map((p, idx) => (
                          <li
                            key={`${exp.key}-p-${idx}`}
                            className="break-words"
                          >
                            {p}
                          </li>
                        ))}
                      </ul>
                    </div>
                    <div>
                      <div className="text-xs font-medium text-muted-foreground">
                        Avoidance and notes
                      </div>
                      <ul className="mt-2 list-disc space-y-1 pl-5">
                        {exp.avoidanceAndNotes.map((a, idx) => (
                          <li
                            key={`${exp.key}-a-${idx}`}
                            className="break-words"
                          >
                            {a}
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>

                  <div className="mt-3">
                    <div className="text-xs font-medium text-muted-foreground">
                      Prompt additions
                    </div>
                    <div className="group relative mt-2">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="absolute right-1 top-1 z-10 h-6 w-6 opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleCopyPromptAdditions({
                            key: exp.key,
                            text: exp.promptAdditions
                              .map((l) => `- ${l}`)
                              .join("\n"),
                          });
                        }}
                        aria-label="Copy prompt additions"
                      >
                        {copiedPromptKey === exp.key ? (
                          <Check className="h-3.5 w-3.5" />
                        ) : (
                          <Copy className="h-3.5 w-3.5" />
                        )}
                      </Button>
                      <div className="whitespace-pre-wrap rounded-md border bg-background p-2 pr-10 font-mono text-xs text-muted-foreground">
                        {exp.promptAdditions.map((l) => `- ${l}`).join("\n")}
                      </div>
                    </div>
                  </div>
                </CardContent>
              </CollapsibleContent>
            </Collapsible>
            {!isExpanded ? (
              <div className="px-6 pb-4 text-xs text-muted-foreground">
                {exp.possibleProblems.length} problems,{" "}
                {exp.avoidanceAndNotes.length} notes,{" "}
                {exp.promptAdditions.length} prompt additions.
              </div>
            ) : null}
          </Card>
        );
      })}
    </div>
  );
}
