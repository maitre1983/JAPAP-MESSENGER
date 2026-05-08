import { createEntityAdapter, createSlice } from "@reduxjs/toolkit";
import _ from "lodash";

const storiesAdaptor = createEntityAdapter({});

export const stackedItems = ({ userStore }) => {
  return userStore.stackedItems;
};

const userStoreSlice = createSlice({
  name: "userStore",
  initialState: storiesAdaptor.getInitialState({
    stackedItems: [],
  }),

  reducers: {
    updateStackedItems: (state, action) => {
      const item = _.first(action.payload);
      const pool = item?.pool;
      const newItems = state.stackedItems.filter(
        (s) => s.pool?.id !== pool?.id
      );
      const items = _.uniqBy([...newItems, ...action.payload], "id");

      state.stackedItems = items;
    },
  },
});

export const { updateStackedItems } = userStoreSlice.actions;

export const reducer = userStoreSlice.reducer;
