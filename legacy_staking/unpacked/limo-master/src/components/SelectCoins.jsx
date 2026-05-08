import { COINS } from "@core/constants/coins";
import { Formatter } from "@core/services/formatter";
import { stakeAbi, stakeAddress } from "@libs/stake-contract";
import { useStakeHolderStore } from "@store/StakerStore";
import _ from "lodash";
import React, { useEffect, useMemo, useState } from "react";
import { Col, Row } from "react-bootstrap";
import { useAccount, useContractReads } from "wagmi";
import Web3 from "web3";
import LIMOCOIN32 from "./../images/limo-coin32.svg";
import PairCard from "./PairCard";
import PriceCard from "./PriceCard";
import StakingCard from "./StakingCard";
const contract = {
  address: stakeAddress,
  abi: stakeAbi,
};

const SelectCoins = () => {
  const { address } = useAccount();

  const [pools, setPools] = useState([]);
  const [reads, setReads] = useState([]);

  const [currentPair, setCurrentPair] = useState({});

  const stakeHolderStore = useStakeHolderStore();

  const currentTotalStaked = useMemo(() => {
    return (currentPair?.pools || []).reduce((acc, pool) => {
      return acc + parseFloat(pool?.stakedTokens);
    }, 0);
  }, [currentPair]);

  const contractReads = useContractReads({
    contracts: [{ ...contract, functionName: "poolCount" }],
  });

  const poolReads = useContractReads({
    contracts: [...reads],
  });

  const contractData = contractReads?.data;
  const poolData = poolReads?.data;

  useEffect(() => {
    if (contractData?.length == 1) {
      const poolCount = parseFloat(contractData[0].result);

      let reads = [];
      for (let i = 0; i < poolCount; i++) {
        reads.push({
          ...contract,
          functionName: "pools",
          args: [i],
        });
      }

      setReads(reads);
    }
  }, [contractData, address]);

  useEffect(() => {
    if (poolData?.length > 0) {
      const pools = poolData
        .map((pool) => {
          const result = pool?.result;
          if (!result) return null;

          const minStakeLimit = Web3.utils.fromWei(result[4], "ether");
          const stakedTokens = Web3.utils.fromWei(result[9], "ether");

          return {
            name: result[0],
            id: parseFloat(result[1]),
            apy: parseFloat(result[2]),
            earlyUnStakeFee: parseFloat(result[3]),
            minStakingLimit: parseInt(minStakeLimit),
            stakeToken: result[5],
            rewardToken: result[6],
            startTime: parseFloat(result[7]),
            duration: parseFloat(result[8]), //poolValidityPeriod
            stakedTokens: parseInt(stakedTokens),
            started: result[10],
            unStakingPaused: result[11],
          };
        })
        .filter((p) => p);
      const newPools = _.sortBy(pools, ["duration"]);
      setPools(newPools);
    }
  }, [poolData, address]);

  const pairs = useMemo(() => {
    let newPairs = {};

    for (let i = 0; i < pools.length; i++) {
      const pool = pools[i];
      const { stakeToken, rewardToken } = pool;

      const rewardCoin = COINS.find((coin) => coin.address == rewardToken);
      const stakeCoin = COINS.find((coin) => coin.address == stakeToken);

      const pairKey = `${stakeCoin.name}-${rewardCoin.name}`;
      if (!newPairs[pairKey]) {
        newPairs[pairKey] = {
          stakeCoin,
          rewardCoin,
          id: pairKey,
        };
      }
    }

    newPairs = Object.values(newPairs);

    return newPairs.map((pair) => {
      const { stakeCoin, rewardCoin } = pair;
      const pairPools = pools.filter(
        (pool) =>
          pool.stakeToken == stakeCoin.address &&
          pool.rewardToken == rewardCoin.address
      );

      return { ...pair, pools: pairPools };
    });
  }, [pools]);

  const stakedCoins = useMemo(() => {
    let staked = {};

    pairs.forEach((pair) => {
      const { stakeCoin } = pair;
      const pairPools = pair?.pools || [];
      const primaryKey = stakeCoin?.address;

      let totalStaked = staked[primaryKey] || 0;
      totalStaked += (pairPools || []).reduce((acc, pool) => {
        return acc + _.round(pool?.stakedTokens, 2);
      }, 0);

      staked[primaryKey] = totalStaked;
    });

    return staked;
  }, [pairs]);

  const totalStaked =
    stakedCoins[currentPair?.stakeCoin?.address] || currentTotalStaked;

  useEffect(() => {
    if (pairs.length > 0) {
      setCurrentPair(pairs[0]);
    }
  }, [pairs.length]);

  const price = currentPair?.stakeCoin?.price || 1;

  useEffect(() => {
    if (currentPair?.stakeCoin?.address) {
      stakeHolderStore.fetchStakers(currentPair?.stakeCoin?.address);
    }
  }, [currentPair]);

  return (
    <>
      {/* <Row className="rm">
        <Col>
          <DropdownButton
            id="dropdown-basic-button"
            className="dropdown-button"
            title={
              `${currentPair?.stakeCoin?.name} - ${currentPair?.rewardCoin?.name}` ||
              "Select Staking Pair"
            }
            name="selectCoin"
          >
            {pairs.map((pair) => {
              const { stakeCoin, rewardCoin } = pair;
              return (
                <Dropdown.Item value="one" onClick={() => setCurrentPair(pair)}>
                  <img src={stakeCoin.image} alt="" className="drop-image" />
                  <FiChevronRight />
                  <img src={rewardCoin.image} alt="" className="drop-image" />
                </Dropdown.Item>
              );
            })}
          </DropdownButton>
        </Col>
      </Row> */}

      {pairs?.map((pair) => (
        <React.Fragment key={pair.id}>
          <Row
            className="row-selection-2"
            style={{
              display: pair?.id == currentPair?.id ? "" : "none",
            }}
          >
            <Col md="7" className="row-selection-col1">
              <PairCard pair={currentPair} />
            </Col>
            <Col sm="12" md="5" className="total-limo-staked-col">
              <div className="total-limo-staked1">
                <PriceCard
                  title={`Total ${currentPair?.stakeCoin?.name || ""} Staked`}
                  balance={Formatter.kFormat(totalStaked) || "..."}
                  image={LIMOCOIN32}
                />
              </div>
              <div className="total-limo-staked2">
                <PriceCard
                  title={`Total ${currentPair?.stakeCoin?.name || ""} Value`}
                  balance={`$ ${
                    Formatter.kFormat(
                      parseFloat(totalStaked * price).toFixed(2)
                    ) || "..."
                  }`}
                  image={LIMOCOIN32}
                />
              </div>
            </Col>
          </Row>
          <Row
            className="row-selection-3"
            style={{
              display: pair?.id == currentPair?.id ? "" : "none",
            }}
          >
            {pair?.pools.map((pool) => {
              const { name, id, apy, duration, earlyUnStakeFee } = pool;
              return (
                <Col md="4" className="row3-col">
                  <StakingCard
                    pool={pool}
                    pair={pair}
                    title={`${duration} MONTHS STAKING`}
                    APY={`${apy}% APY`}
                    limoLimit="Min 100k Limo"
                    interest={`Early withdrawal fee ${earlyUnStakeFee}%`}
                  />
                </Col>
              );
            })}
          </Row>
        </React.Fragment>
      ))}
    </>
  );
};

export default SelectCoins;
