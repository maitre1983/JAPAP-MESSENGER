import { Button, Spinner } from "react-bootstrap";

export const LoadingButton = (props) => {
  const {
    stakeTxt,
    onClick,
    unStakeClicked,
    unstakeTxt,
    loading = false,
    disabled = false,
    pair,
    pool,
    ...rest
  } = props;

  return (
    <div>
      <Button
        className="stake-button"
        onClick={onClick}
        disabled={loading || props.disabled}
        style={{
          display: "flex",
          gap: "5px",
          justifyContent: "center",
          alignItems: "center",
        }}
        {...rest}
      >
        {loading && <Spinner animation="border" variant="light" size="sm" />}
        {props?.children || stakeTxt}
      </Button>
    </div>
  );
};
