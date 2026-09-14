import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(
  new URL("../src/components/workspace/types.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { api, ApiError } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);

for (const [path, method, body] of [
  [
    "admin-chat/sessions/abc/turns",
    "POST",
    { text: "mesaj yoll", client_message_id: "same-message" },
  ],
  [
    "admin-chat/sessions/abc/workflows/wf/actions",
    "POST",
    { action: "complete", client_operation_id: "same-operation", expected_revision: 3 },
  ],
  ["admin-chat/sessions/abc/messages", "GET", undefined],
]) {
  test(`reconnect preserves exact request: ${path}`, async (t) => {
    const calls = [];
    t.mock.method(globalThis, "fetch", async (...args) => {
      calls.push(args);
      if (calls.length === 1) throw new TypeError("Failed to fetch");
      return Response.json({ saved: true });
    });
    assert.deepEqual(await api(path, method, body), { saved: true });
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[0], calls[1]);
  });
}

test("does not retry session creation or arbitrary writes", async (t) => {
  let count = 0;
  t.mock.method(globalThis, "fetch", async () => {
    count++;
    throw new TypeError("Failed to fetch");
  });
  await assert.rejects(
    api("admin-chat/sessions", "POST", {}),
    (e) => e instanceof ApiError && e.status === 0,
  );
  assert.equal(count, 1);
  await assert.rejects(api("agents", "POST", { client_message_id: "not-an-idempotent-route" }));
  assert.equal(count, 2);
});

test("does not retry HTTP validation or permission errors", async (t) => {
  let count = 0;
  t.mock.method(globalThis, "fetch", async () => {
    count++;
    return Response.json({ detail: "Denied" }, { status: 403 });
  });
  await assert.rejects(
    api("admin-chat/sessions/abc/turns", "POST", { client_message_id: "same" }),
    (e) => e.status === 403,
  );
  assert.equal(count, 1);
});

test("persistent network failure stops after one retry with readable error", async (t) => {
  let count = 0;
  t.mock.method(globalThis, "fetch", async () => {
    count++;
    throw new TypeError("Failed to fetch");
  });
  await assert.rejects(
    api("admin-chat/sessions"),
    (e) => e.status === 0 && e.message.includes("Bağlantı kesildi"),
  );
  assert.equal(count, 2);
});

for (const status of [200, 502, 504]) {
  test(`HTML gateway response is a readable error without replay: ${status}`, async (t) => {
    let calls = 0;
    t.mock.method(globalThis, "fetch", async () => {
      calls++;
      return new Response("<!DOCTYPE html><h1>Gateway timeout</h1>", { status });
    });
    await assert.rejects(
      api("admin-chat/sessions/abc/turns", "POST", { client_message_id: "same" }),
      (e) => e instanceof ApiError && !e.message.includes("DOCTYPE") && e.message.includes("kontrol edin"),
    );
    assert.equal(calls, 1);
  });
}
