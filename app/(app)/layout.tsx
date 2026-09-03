import { AppSidebar } from "@/app/components/app-sidebar";
import { requireSession } from "@/app/lib/session";

export default async function AuthenticatedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await requireSession();

  return <AppSidebar session={session}>{children}</AppSidebar>;
}
