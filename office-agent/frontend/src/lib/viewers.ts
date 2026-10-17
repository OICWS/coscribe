import { isDocx } from "./docxReview";
import { isPptx } from "./slides";

/** A file the viewer panel opens instead of the program it belongs to. */
export const isViewable = (path: string) => isDocx(path) || isPptx(path);
