import { Cursor } from "@cursor/sdk";

const result = await Cursor.auth.login({ apiKeyName: "snip2md" });
console.log("Signed in with your Cursor subscription.");
if (result?.email) {
  console.log("Account:", result.email);
}
console.log("You can close this window and go back to Snip2MD.");
