import "dotenv/config";

function readRequired(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `Missing required environment variable ${name}. Copy .env.example to .env and set it there.`,
    );
  }
  return value;
}

export const env = {
  get ELEVENLABS_API_KEY() {
    return readRequired("ELEVENLABS_API_KEY");
  },
  get TOOL_WEBHOOK_SECRET() {
    return readRequired("TOOL_WEBHOOK_SECRET");
  },
  POST_CALL_WEBHOOK_SECRET: process.env.POST_CALL_WEBHOOK_SECRET ?? "",
  WEBHOOK_BASE_URL: process.env.WEBHOOK_BASE_URL ?? "http://localhost:3000",
  PORT: Number(process.env.PORT ?? 3000),
};
