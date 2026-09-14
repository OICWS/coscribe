import { expect, test } from "@playwright/test";
import { freshThreadPath, waitForConnected } from "./helpers";

test("dropping a file onto the composer attaches it like the file picker does", async ({ page }) => {
  await page.goto(freshThreadPath("drop"));
  await waitForConnected(page);

  const composer = page.getByTestId("composer");

  // Playwright has no OS-level drag-and-drop API -- the standard way to
  // exercise a drop handler is to build a real DataTransfer in-page (with
  // a real File, not a mock) and dispatch the same dragenter/dragover/drop
  // sequence a browser fires during an actual drag, directly at the
  // target element.
  await composer.evaluate((el) => {
    const file = new File(["dropped file contents"], "dropped.txt", { type: "text/plain" });
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);
    for (const type of ["dragenter", "dragover", "drop"]) {
      el.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer }));
    }
  });

  await expect(page.getByText("dropped.txt")).toBeVisible();
});

test("pasting a large block of text collapses into a removable pill instead of filling the textarea", async ({
  page,
}) => {
  await page.goto(freshThreadPath("paste"));
  await waitForConnected(page);

  const longText = "line of pasted content\n".repeat(60); // well past both the char and line thresholds
  const textarea = page.locator("textarea");
  await textarea.click();

  await textarea.evaluate((el, text) => {
    const dataTransfer = new DataTransfer();
    dataTransfer.setData("text/plain", text);
    el.dispatchEvent(new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData: dataTransfer }));
  }, longText);

  await expect(page.getByText(/Pasted \(/)).toBeVisible();
  await expect(textarea).toHaveValue("");

  // A short paste, by contrast, should be left alone entirely -- no new
  // pill added (the one pill already on screen is from the long paste
  // above; pastes stack like file/image attachments do, so the right
  // check is "still exactly one", not "none"). Note this only checks the
  // threshold decision itself, not that the text actually lands in the
  // box: a script-dispatched ClipboardEvent isn't a trusted user action,
  // so the browser's own default paste-insert never fires for it
  // regardless of whether our handler calls preventDefault(), which makes
  // "does normal paste-insertion still work" untestable this way -- real
  // typing/paste already covers that path, unchanged here.
  await textarea.evaluate((el) => {
    const dataTransfer = new DataTransfer();
    dataTransfer.setData("text/plain", "short paste");
    el.dispatchEvent(new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData: dataTransfer }));
  });
  await expect(page.getByText(/Pasted \(/)).toHaveCount(1);
});
