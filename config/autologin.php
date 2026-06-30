<?php
	session_start();
	require_once(__DIR__ . '/db_cred.php');

	function create_sessid($conn) {
		for(;;) {
			$sessid = (mt_rand(10000000,99999999));
			$res = mysqli_query($conn, 'select count(*) as cnt from sessions where sessid='.$sessid);
			$row = mysqli_fetch_array($res, MYSQLI_ASSOC);
			if($row["cnt"] == 0) break;
		}
		return($sessid);
	}

	// give every new account a default ID/HN pair on the SELECT ID screen
	// instead of leaving it for the game to generate something unreadable
	function ensure_hnpair($conn, $username) {
		$res = mysqli_query($conn, 'select count(*) as cnt from hnpairs where userid="'.$username.'"');
		$row = mysqli_fetch_array($res, MYSQLI_ASSOC);
		if ($row["cnt"] > 0) return;

		$base = strtoupper(substr(preg_replace("/[^A-Za-z0-9]/", "", $username), 0, 6));
		if ($base == "") $base = "PLAYER";
		for (;;) {
			$handle = substr($base, 0, 6);
			while (strlen($handle) < 6) $handle .= mt_rand(0,9);
			$res = mysqli_query($conn, 'select count(*) as cnt from hnpairs where handle="'.$handle.'"');
			$row = mysqli_fetch_array($res, MYSQLI_ASSOC);
			if ($row["cnt"] == 0) break;
			$base = substr($base, 0, 3) . mt_rand(100,999);
		}
		mysqli_query($conn, 'insert into hnpairs (userid, handle, nickname) values("'.$username.'","'.$handle.'","'.$username.'")');
	}

	// the in-game browser's LOGIN button sends a broken GET request like
	// /0000000X/<typed-id> instead of POSTing the form. Treat the last
	// path segment as the username and log them in automatically.
	$path = isset($_SERVER['REDIRECT_URL']) ? $_SERVER['REDIRECT_URL'] : $_SERVER['REQUEST_URI'];
	$parts = explode('/', trim($path, '/'));
	$attempted = end($parts);
	$username = substr(preg_replace("/[^A-Za-z0-9 _]/", "", $attempted), 0, 14);

	if ($username == "") {
		header('Location: CRS-top.jsp');
		exit();
	}

	$res = mysqli_query($conn, 'select count(*) as cnt from users where userid="'.$username.'"');
	$row = mysqli_fetch_array($res, MYSQLI_ASSOC);
	if ($row["cnt"] == 0) {
		mysqli_query($conn, 'insert into users (userid, passwd) values("'.$username.'","autologin")');
	}
	ensure_hnpair($conn, $username);

	mysqli_query($conn, 'delete from sessions where lower(userid) = lower("'.$username.'")');
	$ip = $_SERVER["REMOTE_ADDR"];
	$port = $_SERVER["REMOTE_PORT"];
	$sessid = create_sessid($conn);
	mysqli_query($conn, 'insert into sessions (userid,ip,port,sessid,lastlogin) values(lower("'.$username.'"),"'.$ip.'","'.$port.'","'.$sessid.'",now())');

	header('Location: startsession.php?sessid='.$sessid.'.');
	exit();
?>
