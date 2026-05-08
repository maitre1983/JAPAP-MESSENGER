import { configureStore } from "@reduxjs/toolkit";
import { UserStore } from "./User";

export const storeOld = configureStore({
  reducer: {
    userStore: UserStore.reducer,
  },
});
