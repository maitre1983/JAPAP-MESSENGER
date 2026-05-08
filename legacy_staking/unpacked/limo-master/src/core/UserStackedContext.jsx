import { createContext } from "react";

export const UserStackedContext = createContext({
  stackedItems: [],
  onStackedItems: () => {},
});
