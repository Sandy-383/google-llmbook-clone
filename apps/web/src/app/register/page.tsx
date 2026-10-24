import type { Metadata } from "next";
import { AuthForm } from "@/components/auth-form";

export const metadata: Metadata = { title: "Create account · LLMBook" };

export default function RegisterPage() {
  return <AuthForm mode="register" />;
}
