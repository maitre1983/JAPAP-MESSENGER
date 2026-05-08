import { useEffect, useState } from "react";
import { toast } from "react-toastify";
import {
  useContractWrite,
  usePrepareContractWrite,
  useWaitForTransaction,
} from "wagmi";
import Web3 from "web3";
import { stakeAddress } from "../libs/stake-contract";
import { LoadingButton } from "./LoadingButton";
import { StakeCoinAfterApproved } from "./StakeCoinAfterApproved";

export const StakeButton = (props) => {
  const { pool, pair, disabled = false } = props;

  const [isStacking, setIsStacking] = useState(false);
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

  const transaction = useWaitForTransaction({
    hash: approveData?.hash,
  });

  const transLoading = transaction.isLoading;
  const transData = transaction.data;
  const error = transaction.error;

  useEffect(() => {
    if (approveError) {
      const error = approveError?.toString().split("\n");
      toast(error[0], {
        type: "error",
      });
    }
  }, [approveError]);

  useEffect(() => {
    if (!isStacking && approveData?.hash && transData?.status == "success") {
      setIsStacking(true);
      coinContractWrite.reset();
    }
  }, [approveData?.hash, transData?.status, isStacking]);

  return (
    <>
      {isStacking ? (
        <>
          {console.log("start staking...")}
          <StakeCoinAfterApproved
            {...props}
            onSuccess={() => {
              setIsStacking(false);
              coinContractWrite.reset();
            }}
            onRejected={() => {
              setIsStacking(false);
              coinContractWrite.reset();
            }}
          />
        </>
      ) : (
        <LoadingButton
          loading={isLoading || transLoading}
          onClick={() => {
            coinWrite();
          }}
          disabled={disabled}
        >
          STAKE
        </LoadingButton>
      )}
    </>
  );
};
