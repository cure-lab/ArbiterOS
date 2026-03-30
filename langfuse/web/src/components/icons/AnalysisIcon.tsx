import * as React from "react";
import { type LucideProps } from "lucide-react";
import { cn } from "@/src/utils/tailwind";

const analysisIconSrc = `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/assets/analysis.png`;

export const AnalysisIcon = React.forwardRef<SVGSVGElement, LucideProps>(
  (
    {
      size = 24,
      className,
      // Lucide-specific props we don't want to forward to <svg>
      color: _color,
      strokeWidth: _strokeWidth,
      absoluteStrokeWidth: _absoluteStrokeWidth,
      ...props
    },
    ref,
  ) => {
    return (
      <svg
        ref={ref}
        // `analysis.png` contains transparent padding; crop to match other sidebar icons' visual size.
        // Bounding box (px) in a 200x200 image: (36,35)-(164,165) → scaled into our 256x256 space.
        viewBox="46.08 44.8 163.84 166.4"
        width={size}
        height={size}
        role="img"
        aria-label="Analysis"
        preserveAspectRatio="xMidYMid meet"
        className={cn("dark:invert", className)}
        {...props}
      >
        <image href={analysisIconSrc} x="0" y="0" width="256" height="256" />
      </svg>
    );
  },
);

AnalysisIcon.displayName = "AnalysisIcon";
