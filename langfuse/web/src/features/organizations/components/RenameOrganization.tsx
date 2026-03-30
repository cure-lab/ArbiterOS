import { Button } from "@/src/components/ui/button";
import { Input } from "@/src/components/ui/input";
import { api } from "@/src/utils/api";
import type * as z from "zod/v4";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/src/components/ui/form";
import { projectNameSchema } from "@/src/features/auth/lib/projectNameSchema";
import Header from "@/src/components/layouts/header";
import { usePostHogClientCapture } from "@/src/features/posthog-analytics/usePostHogClientCapture";
import { useHasOrganizationAccess } from "@/src/features/rbac/utils/checkOrganizationAccess";
import { useQueryOrganization } from "@/src/features/organizations/hooks";
import { Card } from "@/src/components/ui/card";
import { LockIcon } from "lucide-react";
import { useSession } from "next-auth/react";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export default function RenameOrganization() {
  const { update: updateSession } = useSession();
  const capture = usePostHogClientCapture();
  const organization = useQueryOrganization();
  const { language } = useLanguage();
  const hasAccess = useHasOrganizationAccess({
    organizationId: organization?.id,
    scope: "organization:update",
  });

  const orgName =
    organization && "name" in organization ? organization.name : "";

  const form = useForm({
    resolver: zodResolver(projectNameSchema),
    defaultValues: {
      name: "",
    },
  });
  const renameOrganization = api.organizations.update.useMutation({
    onSuccess: () => {
      void updateSession();
    },
    onError: (error) => form.setError("name", { message: error.message }),
  });

  function onSubmit(values: z.infer<typeof projectNameSchema>) {
    if (!organization || !hasAccess) return;
    capture("organization_settings:rename_form_submit");
    renameOrganization
      .mutateAsync({
        orgId: organization.id,
        name: values.name,
      })
      .then(() => {
        form.reset();
      })
      .catch((error) => {
        console.error(error);
      });
  }

  return (
    <div>
      <Header title={localize(language, "Organization Name", "组织名称")} />
      <Card className="mb-4 p-3">
        {form.getValues().name !== "" ? (
          <p className="mb-4 text-sm text-primary">
            {localize(
              language,
              'Your Organization will be renamed from "',
              '你的组织将从 "',
            )}
            {orgName}
            {localize(language, '" to "', '" 重命名为 "')}
            <b>{form.watch().name}</b>
            {'"'}
            {localize(language, ".", "。")}
          </p>
        ) : (
          <p className="mb-4 text-sm">
            {localize(
              language,
              'Your Organization is currently named "',
              '你的组织当前名称为 "',
            )}
            <b>{orgName}</b>
            {'"'}
            {localize(language, ".", "。")}
          </p>
        )}
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="flex-1"
            id="rename-organization-form"
          >
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormControl>
                    <div className="relative">
                      <Input
                        placeholder={orgName}
                        {...field}
                        className="flex-1"
                        disabled={!hasAccess}
                      />
                      {!hasAccess && (
                        <span
                          title={localize(language, "No access", "无访问权限")}
                        >
                          <LockIcon className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 transform text-muted" />
                        </span>
                      )}
                    </div>
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            {hasAccess && (
              <Button
                variant="secondary"
                type="submit"
                loading={renameOrganization.isPending}
                disabled={form.getValues().name === "" || !hasAccess}
                className="mt-4"
              >
                {localize(language, "Save", "保存")}
              </Button>
            )}
          </form>
        </Form>
      </Card>
    </div>
  );
}
