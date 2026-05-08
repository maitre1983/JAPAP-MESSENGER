import { COINS } from "@core/constants/coins";
import { Formatter } from "@core/services/formatter";
import { useStakeHolderStore } from "@store/StakerStore";
import { UserStore } from "@store/User";
import _ from "lodash";
import { useEffect, useState } from "react";
import { Col, Row } from "react-bootstrap";
import { useDispatch, useSelector } from "react-redux";
import { useAccount, useContractReads } from "wagmi";
import Web3 from "web3";
import PriceCard from "./PriceCard";

const StakingBalance = () => {
  const { address } = useAccount();
  const dispatch = useDispatch();
  const stackedItems = useSelector(UserStore.stackedItems);
  const stakeHolderStore = useStakeHolderStore();

  const [loading, setLoading] = useState(true);
  const [limoBalance, setLimoBalance] = useState(0);
  const [currentCoin, setCurrentCoin] = useState(COINS[0]);
  const [tokenStackedItems, setTokenStackedItems] = useState([]);

  const [stakedReward, setStakedReward] = useState(0);

  const userHoldings = stakeHolderStore.items;

  const currentCoinHolder = userHoldings?.find(
    (u) => u.token == currentCoin?.address && u.address == address
  );

  const userBalanceRead = useContractReads({
    contracts: [
      {
        address: currentCoin?.address,
        abi: currentCoin?.abi,
        functionName: "balanceOf",
        args: [address],
      },
    ],
  });

  const balanceData = userBalanceRead?.data;

  const unStakedItems = tokenStackedItems?.filter((item) => item?.unstaked);
  const stakedItems = tokenStackedItems?.filter((item) => !item?.unstaked);

  const rewards = unStakedItems?.reduce((acc, item) => {
    return acc + parseFloat(item?.reward);
  }, 0);

  const totalStaked = tokenStackedItems
    ?.filter((s) => !s?.unstaked)
    ?.reduce((acc, item) => {
      return acc + parseFloat(item?.stakedAmount);
    }, 0);

  const limoCoin = COINS?.find((item) => item?.address == currentCoin?.address);
  const price = limoCoin?.price || 1;

  // const rewardPrice = parseFloat(rewards * price).toFixed(5);
  const totalReward = _.round(rewards + stakedReward, 5);
  const rewardPrice = _.round((rewards + stakedReward) * price, 5);

  useEffect(() => {
    const interval = setInterval(() => {
      const expectedRewards = stakedItems?.reduce((acc, item) => {
        return acc + Formatter.calculateStakedReward(item);
      }, 0);
      setStakedReward(expectedRewards);
    }, 1000);
    return () => {
      clearInterval(interval);
    };
  }, [stakedItems?.length]);

  useEffect(() => {
    if (stackedItems?.length > 0) {
      const tokenStackedItems = stackedItems?.filter(
        (item) => item?.pool?.stakeToken == currentCoin?.address
      );
      setTokenStackedItems(tokenStackedItems);
    }
  }, [currentCoin, stackedItems]);

  useEffect(() => {
    if (balanceData?.length == 1) {
      const balance = Web3.utils.fromWei(balanceData[0]?.result || 0, "ether");
      setLimoBalance(parseFloat(balance).toFixed(2));
    }
  }, [balanceData]);

  useEffect(() => {
    if (currentCoin?.address && totalStaked && !loading) {
      if (currentCoinHolder) {
        stakeHolderStore
          .updateItem({
            id: currentCoinHolder?.id,
            quantity: totalStaked,
          })
          .then(() => {
            stakeHolderStore.fetchUserItems(address);
            stakeHolderStore.fetchStakers(currentCoin?.address);
          });
      } else {
        stakeHolderStore
          .createItem({
            address: address,
            token: currentCoin?.address,
            tokenName: currentCoin?.name,
            quantity: totalStaked,
          })
          .then(() => {
            stakeHolderStore.fetchUserItems(address);
            stakeHolderStore.fetchStakers(currentCoin?.address);
          });
      }
    }
  }, [currentCoin, totalStaked, currentCoinHolder?.address, loading]);

  useEffect(() => {
    if (address) {
      setLoading(true);
      stakeHolderStore.fetchUserItems(address).then(() => {
        setLoading(false);
      });
    }
  }, [address, totalStaked]);

  return (
    <>
      {/* <Row className="rm" style={{ marginBottom: 10 }}>
        <Col>
          <DropdownButton
            id="dropdown-basic-button"
            className="dropdown-button"
            title={currentCoin.name || "Select Staking Pair"}
            name="selectCoin"
          >
            {COINS.map((coin) => {
              return (
                <Dropdown.Item value="one" onClick={() => setCurrentCoin(coin)}>
                  <img src={coin.image} alt="" className="drop-image" />{" "}
                  {coin.name}
                </Dropdown.Item>
              );
            })}
          </DropdownButton>
        </Col>
      </Row> */}
      <Row className="row-earning">
        <Col md="4" className="row-earning-col">
          <PriceCard
            title={`${currentCoin?.name} Balance`}
            balance={Formatter.kFormat(limoBalance) || "..."}
            image={currentCoin?.image}
          />
        </Col>
        <Col md="4" className="row-earning-col">
          <PriceCard
            title={`Staked ${currentCoin?.name} Balance`}
            balance={Formatter.kFormat(totalStaked) || "..."}
            image={currentCoin?.image}
          />
        </Col>
        <Col md="4" className="row-earning-col">
          <PriceCard
            title="Earned Value"
            balance={`${Formatter.kFormat(totalReward) || "..."}`}
            image={currentCoin?.image}
          />
        </Col>
      </Row>
    </>
  );
};

export default StakingBalance;
