import { Button } from "@/src/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/src/components/ui/dialog";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/src/components/ui/form";
import { Input } from "@/src/components/ui/input";
import { api } from "@/src/utils/api";
import * as z from "zod/v4";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { usePostHogClientCapture } from "@/src/features/posthog-analytics/usePostHogClientCapture";
import {
  hasOrganizationAccess,
  useHasOrganizationAccess,
} from "@/src/features/rbac/utils/checkOrganizationAccess";
import { useQueryProject } from "@/src/features/projects/hooks";
import { useSession } from "next-auth/react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/src/components/ui/select";
import { Alert, AlertDescription, AlertTitle } from "@/src/components/ui/alert";
import { TriangleAlert } from "lucide-react";
import { showSuccessToast } from "@/src/features/notifications/showSuccessToast";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export function TransferProjectButton() {
  const capture = usePostHogClientCapture();
  const session = useSession();
  const { project, organization } = useQueryProject();
  const { language } = useLanguage();
  const hasAccess = useHasOrganizationAccess({
    organizationId: organization?.id,
    scope: "projects:transfer_org",
  });
  const allOrgs = session.data?.user?.organizations ?? [];
  const organizationsToTransferTo =
    allOrgs.filter((org) =>
      hasOrganizationAccess({
        session: session.data,
        organizationId: org.id,
        scope: "projects:transfer_org",
      }),
    ) ?? [];
  const confirmMessage = (organization?.name + "/" + project?.name)
    .replaceAll(" ", "-")
    .toLowerCase();

  const formSchema = z.object({
    name: z.string().includes(confirmMessage, {
      message: localize(
        language,
        `Please confirm with "${confirmMessage}"`,
        `请输入 "${confirmMessage}" 以确认`,
      ),
    }),
    projectId: z.string(),
  });

  const transferProject = api.projects.transfer.useMutation({
    onSuccess: async () => {
      showSuccessToast({
        title: localize(language, "Project transferred", "项目已转移"),
        description: localize(
          language,
          "The project is successfully transferred to the new organization. Redirecting...",
          "项目已成功转移到新组织。正在跳转...",
        ),
      });
      await new Promise((resolve) => setTimeout(resolve, 5000));
      void session.update();
      window.location.href = "/";
    },
  });

  const form = useForm({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: "",
      projectId: "",
    },
  });

  const onSubmit = (values: z.infer<typeof formSchema>) => {
    if (!project) return;
    capture("project_settings:project_delete");
    transferProject.mutate({
      projectId: project.id,
      targetOrgId: values.projectId,
    });
  };

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="destructive-secondary" disabled={!hasAccess}>
          {localize(language, "Transfer Project", "转移项目")}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle className="text-lg font-semibold">
            {localize(language, "Transfer Project", "转移项目")}
          </DialogTitle>
          <Alert className="mt-2">
            <TriangleAlert className="h-4 w-4" />
            <AlertTitle>{localize(language, "Warning", "警告")}</AlertTitle>
            <AlertDescription>
              {localize(
                language,
                "Transferring the project will move it to a different organization:",
                "转移项目会将其移动到另一个组织：",
              )}
              <ul className="list-disc pl-4">
                <li>
                  {localize(
                    language,
                    "Members who are not part of the new organization will lose access.",
                    "不属于新组织的成员将失去访问权限。",
                  )}
                </li>
                <li>
                  {localize(
                    language,
                    "The project remains fully operational as API keys, settings, and data will remain unchanged. All features (e.g. tracing, prompt management) will continue to work without interruption.",
                    "项目会继续正常运行，因为 API 密钥、设置和数据都不会改变。所有功能（如 tracing、prompt management）都会持续可用，不会中断。",
                  )}
                </li>
              </ul>
            </AlertDescription>
          </Alert>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
            <DialogBody>
              <FormField
                control={form.control}
                name="projectId"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {localize(
                        language,
                        "Select New Organization",
                        "选择新组织",
                      )}
                    </FormLabel>
                    <FormControl>
                      <Select
                        onValueChange={field.onChange}
                        value={field.value}
                        disabled={transferProject.isPending}
                      >
                        <SelectTrigger>
                          <SelectValue
                            placeholder={localize(
                              language,
                              "Select organization",
                              "选择组织",
                            )}
                          />
                        </SelectTrigger>
                        <SelectContent>
                          {organizationsToTransferTo
                            .filter((org) => org.id !== organization?.id)
                            .map((org) => (
                              <SelectItem key={org.id} value={org.id}>
                                {org.name}
                              </SelectItem>
                            ))}
                        </SelectContent>
                      </Select>
                    </FormControl>
                    <FormDescription>
                      {localize(
                        language,
                        "Transfer this project to another organization where you have the ability to create projects.",
                        "将此项目转移到另一个你拥有创建项目权限的组织。",
                      )}
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {localize(language, "Confirm", "确认")}
                    </FormLabel>
                    <FormControl>
                      <Input placeholder={confirmMessage} {...field} />
                    </FormControl>
                    <FormDescription>
                      {localize(
                        language,
                        `To confirm, type "${confirmMessage}" in the input box.`,
                        `要确认，请在输入框中输入 "${confirmMessage}"。`,
                      )}
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </DialogBody>
            <DialogFooter>
              <Button
                type="submit"
                variant="destructive"
                loading={transferProject.isPending}
                className="w-full"
              >
                {localize(language, "Transfer project", "转移项目")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
