"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

const internalOrigin =
  process.env.FASTAPI_INTERNAL_URL ?? "http://127.0.0.1:8000";

export async function logoutAction() {
  const cookieStore = await cookies();

  await fetch(`${internalOrigin}/api/v1/auth/logout`, {
    method: "POST",
    headers: {
      cookie: cookieStore.toString(),
    },
    cache: "no-store",
  });

  redirect("/login");
}
