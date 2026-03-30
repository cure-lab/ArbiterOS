import { ChevronDownIcon } from "lucide-react";

import { Button } from "@/src/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/src/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/src/components/ui/select";
import { PriceUnit } from "@/src/features/models/validation";
import { usePriceUnitMultiplier } from "@/src/features/models/hooks/usePriceUnitMultiplier";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export const PriceUnitSelector = () => {
  const { priceUnit, setPriceUnit } = usePriceUnitMultiplier();
  const { language } = useLanguage();
  const getLocalizedUnit = (unit: PriceUnit) => {
    switch (unit) {
      case PriceUnit.PerUnit:
        return localize(language, "per unit", "按单位");
      case PriceUnit.Per1KUnits:
        return localize(language, "per 1K units", "每 1K 单位");
      case PriceUnit.Per1MUnits:
        return localize(language, "per 1M units", "每 1M 单位");
      default:
        return unit;
    }
  };

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button size="icon" variant="ghost">
          <ChevronDownIcon className="h-4 w-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[200px] p-0">
        <Select
          value={priceUnit}
          onValueChange={(value: PriceUnit) => setPriceUnit(value)}
        >
          <SelectTrigger className="w-full">
            <SelectValue
              placeholder={localize(language, "Select unit", "选择单位")}
            />
          </SelectTrigger>
          <SelectContent>
            {Object.values(PriceUnit).map((unit) => (
              <SelectItem key={unit} value={unit}>
                {getLocalizedUnit(unit)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </PopoverContent>
    </Popover>
  );
};
