/* shadcn/ui "toaster" (MIT, https://ui.shadcn.com), written by hand; the close label is translated. */
"use client"

import { useLanguage } from "@/components/shell/language-provider"
import { Toast, ToastClose, ToastDescription, ToastProvider, ToastTitle, ToastViewport } from "@/components/ui/toast"
import { useToast } from "@/components/ui/use-toast"

export function Toaster() {
  const { toasts } = useToast()
  const { t } = useLanguage()
  return (
    <ToastProvider>
      {toasts.map(({ id, title, description, action, ...props }) => (
        <Toast key={id} {...props}>
          <div className="grid gap-1">
            {title ? <ToastTitle>{title}</ToastTitle> : null}
            {description ? <ToastDescription>{description}</ToastDescription> : null}
          </div>
          {action}
          <ToastClose label={t("toast.close")} />
        </Toast>
      ))}
      <ToastViewport />
    </ToastProvider>
  )
}
