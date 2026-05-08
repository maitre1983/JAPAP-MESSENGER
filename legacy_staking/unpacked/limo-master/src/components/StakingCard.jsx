import useDebounce from "@core/hooks/useDebounce";
import { Formatter } from "@core/services/formatter";
import { UserStore } from "@store/User";
import { useEffect, useState } from "react";
import { Form } from "react-bootstrap";
import { useDispatch } from "react-redux";
import { useAccount, useContractReads } from "wagmi";
import Web3 from "web3";
import { stakeAbi, stakeAddress } from "../libs/stake-contract";
import { LoadingButton } from "./LoadingButton";
import { StakeButton } from "./StakeButton";
import UnStakePopUp from "./UnStakePopUp";

const StakingCard = ({ pool, pair }) => {
  const [quantity, setQuantity] = useState(0);
  const { address } = useAccount();
  const [stakedData, setStakedData] = useState([]);
  const [reads, setReads] = useState([]);
  const [show, setShow] = useState(false);
  const dispatch = useDispatch();

  const debouncedValue = useDebounce(quantity, 1000);

  const unStakeAbles = stakedData?.filter((s) => !s?.unstaked);

  const minQuantity = pool?.minStakingLimit || 100;

  const contract = {
    address: stakeAddress,
    abi: stakeAbi,
  };

  const stakerCountReads = useContractReads({
    contracts: [
      { ...contract, functionName: "stakerindex", args: [address, pool.id] },
    ],
    watch: true,
  });

  const stakedContractReads = useContractReads({
    contracts: [...reads],
    watch: true,
  });

  const stakerData = stakedContractReads?.data;
  const stakerCountData = stakerCountReads?.data;

  useEffect(() => {
    if (stakerCountData?.length > 0) {
      const count = parseFloat(stakerCountData[0].result);

      let reads = [];
      for (let i = 0; i < count; i++) {
        reads.push({
          ...contract,
          functionName: "users",
          args: [address, pool.id, i],
        });
      }

      setReads(reads);
    }
  }, [stakerCountData]);

  useEffect(() => {
    if (stakerData?.length > 0) {
      const data = stakerData.map((staker, index) => {
        const result = staker?.result;
        return {
          id: `${pool?.id}-${index}`,
          stakedAmount: Web3.utils.fromWei(result[0], "ether"),
          stakedTime: parseFloat(result[1]),
          reward: Web3.utils.fromWei(result[2], "ether"),
          unstaked: result[3],
          index: index,
        };
      });

      setStakedData(data);
    }
  }, [stakerData]);

  useEffect(() => {
    if (minQuantity) {
      setQuantity(minQuantity);
    }
  }, [minQuantity]);

  useEffect(() => {
    const fetchedItems = stakedData.map((s) => ({ ...s, pool: pool }));
    setTimeout(() => {
      dispatch(UserStore.updateStackedItems(fetchedItems));
    }, 500);
  }, [pool?.id, stakedData, address]);

  const duration = parseInt(pool.duration / (60 * 60 * 24 * 30));

  return (
    <>
      <h2>{`${duration} MONTHS STAKING`}</h2>
      <h2 className="bold"> {`${pool.apy}% APY`}</h2>
      <h2 className="bold">
        <Form.Control
          type="text"
          className="staking-input"
          value={quantity}
          onChange={(e) => {
            const value = parseFloat(e.target.value);
            if (isNaN(value)) {
              return;
            }
            setQuantity(value);
          }}
        />
        {quantity < minQuantity && (
          <span className="error">Minimum staking limit is {minQuantity} </span>
        )}
      </h2>
      <p className="min-limo"> Min {Formatter.kFormat(minQuantity)} </p>
      <div className="early-withdraw"></div>
      <div className="early-withdraw">
        <p className="light"> Early withdrawal fee {pool.earlyUnStakeFee}%</p>
      </div>

      {quantity < minQuantity ? (
        <LoadingButton disabled={true}>STAKE</LoadingButton>
      ) : (
        <StakeButton
          pair={pair}
          pool={pool}
          quantity={debouncedValue}
          onSuccess={() => {}}
          disabled={quantity < minQuantity}
        />
      )}

      {unStakeAbles?.length > 0 && (
        <>
          <LoadingButton onClick={() => setShow(true)}>UNSTAKE</LoadingButton>
        </>
      )}
      {show && (
        <UnStakePopUp
          show={show}
          stakedItems={unStakeAbles}
          pair={pair}
          pool={pool}
          onSuccess={() => {}}
          onHide={() => setShow(false)}
          onClick={() => setShow(false)}
        />
      )}

      {/* {stakedData
        ?.filter((s) => !s?.unstaked)
        ?.map((data, index) => {
          return (
            <React.Fragment key={index}>
              <UnStakeButton
                pair={pair}
                pool={pool}
                index={data.index}
                stake={data}
                onSuccess={() => {}}
              />
            </React.Fragment>
          );
        })} */}
    </>
  );
};

export default StakingCard;
