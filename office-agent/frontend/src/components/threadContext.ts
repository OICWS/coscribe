import { createContext } from "react";

/** The conversation being shown, for the parts deep in the tree that ask the
 * server about its files (attachments, the shape overlay on a slide preview). */
export const ThreadIdContext = createContext<string>("");
