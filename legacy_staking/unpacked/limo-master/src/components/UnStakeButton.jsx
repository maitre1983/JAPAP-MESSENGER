import { useEffect } from "react";
import { toast } from "react-toastify";
import { LoadingButton } from "./LoadingButton";

import {
  useContractWrite,
  usePrepareContractWrite,
  useWaitForTransaction,
} from "wagmi";
import { stakeAbi, stakeAddress } from "../libs/stake-contract";

export const UnStakeButton = (props) => {
  const { pool, pair, index = 0, stake = {}, ...rest } = props;

  const prepareContractWrite = usePrepareContractWrite({
    address: stakeAddress,
    abi: stakeAbi,
    functionName: "unStake",
    args: [pool?.id, index],
  });

  const unStakeContractWrite = useContractWrite(prepareContractWrite.config);
  const unStakeWrite = unStakeContractWrite.write;
  const data = unStakeContractWrite.data;
  const isLoading = unStakeContractWrite.isLoading;
  const unStakeError = unStakeContractWrite.error;

  const transaction = useWaitForTransaction({
    hash: data?.hash,
  });

  const transLoading = transaction.isLoading;
  const transData = transaction.data;
  const error = transaction.error;

  useEffect(() => {
    if (data?.hash && transData?.status == "success") {
      toast("Congratulations unstaked successfully!", {
        type: "success",
      });
      if (props?.onSuccess) {
        props?.onSuccess();
      }
    }
  }, [transData, data?.hash]);

  useEffect(() => {
    if (unStakeError) {
      const errorExtract = unStakeError?.toString().split("\n");
      toast(errorExtract[0], {
        type: "error",
      });
    }
  }, [unStakeError]);

  return (
    <>
      <LoadingButton
        loading={isLoading || transLoading}
        onClick={() => {
          if (typeof unStakeWrite !== "function") {
            toast("Something went wrong please contact to owner!", {
              type: "error",
            });
          } else {
            unStakeWrite();
          }
        }}
        {...rest}
        disabled={!unStakeWrite || isLoading || transLoading}
      >
        {props?.children || "UNSTAKE"}
      </LoadingButton>
    </>
  );
};
