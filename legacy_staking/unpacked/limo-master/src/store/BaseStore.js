import _ from "lodash";
import { useSelector } from "react-redux";
import { useFirestore } from "react-redux-firebase";

export function useBaseStore(collection) {
  const firestore = useFirestore();

  const items = _.sortBy(
    _.values(
      useSelector((state) => {
        return state.firestore.data[collection];
      })
    ).filter((i) => i),
    ["quantity"]
  );

  const selectItem = (id) => {
    return items && items.filter((i) => i).find((i) => i.id === id);
  };

  const createItem = (data) => {
    return firestore.add(collection, {
      ...data,
    });
  };

  const deleteItem = (id) => {
    return firestore.delete(`${collection}/${id}`);
  };

  const updateItem = (data) => {
    return firestore.update(`${collection}/${data.id}`, {
      ...data,
    });
  };

  const getItem = (id) => {
    return firestore.get(`${collection}/${id}`);
  };

  const fetchItems = () => {
    return firestore.get(collection);
  };

  const getItemsByQuery = (query, orderBy, limit, storeAs = null) => {
    return firestore.get({
      collection,
      where: query,
      orderBy: orderBy,
      limit: limit,
      storeAs: storeAs || collection,
    });
    const fireQuery = firestore.collection(collection);

    query.forEach((q) => {
      fireQuery.where(q[0], q[1], q[2]);
    });

    return fireQuery.get();
  };

  return {
    createItem,
    deleteItem,
    updateItem,
    getItem,
    fetchItems,
    selectItem,
    getItemsByQuery,
    items,
    firestore,
  };
}
