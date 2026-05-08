import _ from "lodash";

export const upsertOneItem = (items, item) => {
  if (!item) {
    return items;
  }
  const index = items.findIndex((i) => i.id == item.id);
  if (index > -1) {
    return items.map((i) => {
      if (i.id == item.id) {
        return item;
      }
      return i;
    });
  } else {
    return _.sortBy(_.uniqBy(_.flatten(_.concat([item], items)), "id"), "id");
  }
};

export const deleteItems = (items, id) => {
  if (!id) {
    return items;
  }
  const removeIds = Array.isArray(id) ? id : [id];
  return items.filter((i) => !removeIds.includes(i.id));
};

export const selectItem = (items, id) => {
  return items.find((i) => i.id == id);
};

export const appendItems = (items, newItems) => {
  if (!newItems || newItems.length == 0) return items;
  return _.uniqBy(_.flatten(_.concat(items, newItems)), "id");
};
