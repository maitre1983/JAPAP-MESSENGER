import moment from "moment";

export function kFormat(num) {
  return Math.abs(num) > 999
    ? Math.sign(num) * (Math.abs(num) / 1000).toFixed(1) + "K"
    : Math.sign(num) * Math.abs(num);
}

export function calculateStakedReward(stakedItem) {
  const { pool, stakedAmount, stakedTime } = stakedItem;

  const apy = pool?.apy || 0;
  const startTime = moment(stakedTime * 1000);
  const waitedTime = moment().diff(startTime);
  const waitedSeconds = waitedTime / 1000;

  const expectedReward = (stakedAmount * apy) / 100;
  const perSecondReward = expectedReward / pool?.duration;

  const reward = perSecondReward * waitedSeconds;

  return reward;
}
