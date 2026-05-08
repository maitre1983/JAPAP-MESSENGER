import { useEffect, useState } from "react";
import { toast } from "react-toastify";
import {
  useContractWrite,
  usePrepareContractWrite,
  useWaitForTransaction,
} from "wagmi";
import Web3 from "web3";
import { stakeAbi, stakeAddress } from "../libs/stake-contract";
import { LoadingButton } from "./LoadingButton";

export const StakeCoinAfterApproved = (props) => {
  const { pool, pair, disabled = false } = props;

  const [isStacking, setIsStacking] = useState(false);

  const minQuantity = pool?.minStakingLimit || 100;
  const inputQuantity = parseFloat(props?.quantity || 100);
  const quantity = inputQuantity < minQuantity ? minQuantity : inputQuantity;

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
    hash: stakeData?.hash,
  });

  const transLoading = transaction.isLoading;
  const transData = transaction.data;
  const error = transaction.error;

  useEffect(() => {
    setTimeout(() => {
      if (!stakeData?.hash && !isStacking) {
        setIsStacking(true);
        if (stakeWrite) {
          console.log("staking...");
          stakeWrite().then((res) => {
            // props.onSuccess();
          });
        } else {
          // toast("Something went wrong please contact to owner!", {
          //   type: "error",
          // });
          // props?.onRejected();
        }
      }
    }, 2 * 1000);
  }, [transData, stakeData?.hash, stakeWrite, isStacking]);

  useEffect(() => {
    if (stakeData?.hash && transData?.status == "success") {
      toast(`Staked Successfully!`, {
        type: "success",
      });
      props.onSuccess();
      stakeContractWrite?.reset();
    }
  }, [transData, stakeData?.hash]);

  useEffect(() => {
    if (stakeError) {
      const error = stakeError?.toString().split("\n");
      toast(error[0], {
        type: "error",
      });
      props?.onRejected();
    }
  }, [stakeError]);

  return (
    <>
      <LoadingButton loading={true} disabled={disabled || !stakeWrite}>
        STAKE
      </LoadingButton>
    </>
  );
};
