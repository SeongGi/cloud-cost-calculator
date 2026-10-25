import type { Metadata } from "next";
import { headers } from "next/headers";
import { adminEmail } from "@/lib/admin-auth";
import { AdminPanel } from "./admin-panel";
import "./admin.css";

export const metadata: Metadata = { title: "관리자 · 클라우드 비용 계산기", robots: { index: false } };
export const dynamic = "force-dynamic";

export default async function AdminPage() {
  const email = adminEmail(await headers());
  if (!email) {
    return (
      <main className="admin">
        <h1>관리자</h1>
        <p className="admin-note">관리자 계정으로 로그인해야 볼 수 있습니다.</p>
      </main>
    );
  }
  return <AdminPanel email={email} />;
}
