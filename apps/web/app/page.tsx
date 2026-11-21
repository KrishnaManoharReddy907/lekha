import { redirect } from "next/navigation";

// The public landing page is served at /welcome; signed-in users land on the dashboard.
export default function Home() {
  redirect("/dashboard");
}
