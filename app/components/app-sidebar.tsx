"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import type { AppSession } from "@/app/lib/types";

type SidebarItem = {
  href: string;
  label: string;
  shortLabel: string;
};

const baseItems: SidebarItem[] = [
  { href: "/movies", label: "Movies", shortLabel: "Mv" },
  { href: "/movies/new", label: "New Movie", shortLabel: "New" },
  { href: "/sweeps", label: "Brand Sweep", shortLabel: "Sw" },
  { href: "/logout", label: "Logout", shortLabel: "Out" },
];

const adminItems: SidebarItem[] = [
  { href: "/admin", label: "Admin", shortLabel: "Ad" },
  { href: "/admin/users", label: "Users", shortLabel: "Us" },
  { href: "/admin/videos", label: "Videos", shortLabel: "Vi" },
  { href: "/admin/errors", label: "Errors", shortLabel: "Er" },
];

export function AppSidebar({
  session,
  children,
}: {
  session: AppSession;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const items = session.isAdmin ? [...baseItems, ...adminItems] : baseItems;

  return (
    <div className="app-frame">
      <aside className="sidebar" data-collapsed={collapsed}>
        <div className="row-between">
          {!collapsed ? (
            <div className="sidebar-brand">
              <span className="eyebrow">Clip Create</span>
              <strong>Studio Console</strong>
            </div>
          ) : null}
          <button
            aria-controls="sidebar-nav"
            aria-expanded={!collapsed}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="sidebar-toggle"
            onClick={() => setCollapsed((value) => !value)}
            type="button"
          >
            {collapsed ? ">" : "<"}
          </button>
        </div>

        <nav className="nav-list" id="sidebar-nav">
          {items.map((item) => {
            const active =
              pathname === item.href || pathname.startsWith(`${item.href}/`);

            return (
              <Link
                className="nav-link"
                data-active={active}
                href={item.href}
                key={item.href}
              >
                <span aria-hidden="true" className="mono-label">
                  {item.shortLabel}
                </span>
                <span className={collapsed ? "sr-only" : undefined}>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="sidebar-footer stack-sm">
          {!collapsed ? (
            <>
              <strong>{session.displayName}</strong>
              <span className="text-soft">
                {session.isAdmin ? "Administrator" : "Creator"}
              </span>
            </>
          ) : (
            <span className="mono-label">Me</span>
          )}
        </div>
      </aside>

      <div>{children}</div>
    </div>
  );
}
