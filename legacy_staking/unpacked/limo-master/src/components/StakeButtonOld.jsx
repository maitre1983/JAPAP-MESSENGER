import { useEffect } from "react";
import { toast } from "react-toastify";
import {
  useContractWrite,
  usePrepareContractWrite,
  useWaitForTransaction,
} from "wagmi";
import Web3 from "web3";
import { stakeAbi, stakeAddress } from "../libs/stake-contract";
import { LoadingButton } from "./LoadingButton";
import Notification from "./Notification";

export const StakeButtonOld = (props) => {
  const { pool, pair, disabled = false } = props;

  const minQuantity = pool?.minStakingLimit || 100;
  const inputQuantity = parseFloat(props?.quantity || 100);
  const quantity = inputQuantity < minQuantity ? minQuantity : inputQuantity;

  const prepareCoinContractWrite = usePrepareContractWrite({
    address: pair?.stakeCoin?.address,
    abi: pair?.stakeCoin?.abi,
    functionName: "approve",
    args: [stakeAddress, Web3.utils.toWei(quantity.toString(), "ether")],
  });

  const coinContractWrite = useContractWrite(prepareCoinContractWrite.config);
  const coinWrite = coinContractWrite.write;
  const approveData = coinContractWrite.data;
  const isLoading = coinContractWrite.isLoading;
  const approveError = coinContractWrite.error;

  const prepareStakeContractWrite = usePrepareContractWrite({
    address: stakeAddress,
    abi: stakeAbi,
    functionName: "stake",
    args: [pool?.id, Web3.utils.toWei(quantity.toString(), "ether")],
  });

  const stakeContractWrite = useContractWrite(prepareStakeContractWrite.config);

  const stakeWrite = stakeContractWrite.writeAsync;
  const stakeData = stakeContractWrite.data;
  const stakeLoading = stakeContractWrite.isLoading;
  const stakeError = stakeContractWrite.error;

  const transaction = useWaitForTransaction({
    hash: stakeData?.hash || approveData?.hash,
  });

  const transLoading = transaction.isLoading;
  const transData = transaction.data;
  const error = transaction.error;

  useEffect(() => {
    if (
      approveData?.hash &&
      !stakeData?.hash &&
      transData?.status == "success"
    ) {
      console.log("stakeWrite", stakeWrite);
      if (stakeWrite) {
        stakeWrite().then((res) => {
          coinContractWrite.reset();
        });
      } else {
        toast("Something went wrong please contact to owner!", {
          type: "error",
        });
      }
    }
  }, [
    approveData?.hash,
    transData,
    stakeData?.hash,
    stakeWrite,
    stakeContractWrite?.reset,
  ]);

  useEffect(() => {
    if (stakeData?.hash && transData?.status == "success") {
      toast(`Staked Successfully!`, {
        type: "success",
      });
      props.onSuccess();
    }
  }, [transData, stakeData?.hash]);

  useEffect(() => {
    if (approveError) {
      const error = approveError?.toString().split("\n");
      toast(error[0], {
        type: "error",
      });
    }
  }, [approveError]);

  useEffect(() => {
    if (stakeError) {
      const error = stakeError?.toString().split("\n");
      toast(error[0], {
        type: "error",
      });
    }
  }, [stakeError]);

  return (
    <>
      <LoadingButton
        loading={isLoading || stakeLoading || transLoading}
        onClick={() => {
          if (stakeData?.hash) {
            stakeContractWrite.reset();
          }

          coinWrite();
        }}
        disabled={disabled || !stakeWrite}
      >
        STAKE
      </LoadingButton>
      {approveData?.hash &&
        !stakeData?.hash &&
        transData?.status == "success" && (
          <Notification NotiTitle="Staking Successfull" />
        )}
    </>
  );
};
