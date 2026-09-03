import { redirect } from "next/navigation";
import { getSession } from "@/app/lib/api";

export async function requireSession(options?: { admin?: boolean }) {
  const session = await getSession();

  if (!session) {
    redirect("/login");
  }

  if (options?.admin && !session.isAdmin) {
    redirect("/movies");
  }

  return session;
}
