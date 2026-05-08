import { useBaseStore } from "@store/BaseStore";
import _ from "lodash";
import { useSelector } from "react-redux";

export const useStakeHolderStore = () => {
  const baseStore = useBaseStore("stakeHolders");

  const topStakers = _.orderBy(
    _.values(
      useSelector((state) => {
        return state.firestore.data["topStakers"];
      })
    ).filter((i) => i),
    "quantity",
    "desc"
  );

  const fetchUserItems = (address) => {
    return baseStore.getItemsByQuery([["address", "==", address || ""]]);
  };

  const fetchStakers = (tokenAddress) => {
    return baseStore.getItemsByQuery(
      [["token", "==", tokenAddress || ""]],
      [["quantity", "desc"]],
      20,
      "topStakers"
    );
  };

  return {
    ...baseStore,
    fetchUserItems,
    fetchStakers,
    topStakers,
  };
};
