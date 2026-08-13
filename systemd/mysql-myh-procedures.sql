DROP DATABASE IF EXISTS test;
DELETE FROM mysql.user WHERE User='';
DROP USER IF EXISTS 'root'@'%';
DROP USER IF EXISTS 'root'@'::1';
DROP USER IF EXISTS 'root'@'127.0.0.1';
CREATE DATABASE IF NOT EXISTS myh_admin CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
DROP PROCEDURE IF EXISTS myh_admin.create_tenant_database;
DROP PROCEDURE IF EXISTS myh_admin.drop_tenant_database;
DROP PROCEDURE IF EXISTS myh_admin.reset_tenant_password;
DELIMITER //
CREATE DEFINER='root'@'localhost' PROCEDURE myh_admin.create_tenant_database(
  IN p_database VARCHAR(64), IN p_user VARCHAR(32), IN p_password VARCHAR(255)
) SQL SECURITY DEFINER
BEGIN
  IF p_database NOT REGEXP '^myh_[0-9]+_[a-z0-9_]{1,43}_[0-9a-f]{8}$'
     OR p_user NOT REGEXP '^u[0-9]+_[0-9a-f]{12}$' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Invalid generated tenant identifier';
  END IF;
  SET @create_db = CONCAT('CREATE DATABASE `', p_database, '` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci');
  PREPARE stmt FROM @create_db; EXECUTE stmt; DEALLOCATE PREPARE stmt;
  SET @create_user = CONCAT('CREATE USER ', QUOTE(p_user), '@''172.23.%'' IDENTIFIED BY ', QUOTE(p_password));
  PREPARE stmt FROM @create_user; EXECUTE stmt; DEALLOCATE PREPARE stmt;
  SET @grant_user = CONCAT('GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, CREATE TEMPORARY TABLES, LOCK TABLES, EXECUTE, CREATE VIEW, SHOW VIEW, TRIGGER ON `', p_database, '`.* TO ', QUOTE(p_user), '@''172.23.%''');
  PREPARE stmt FROM @grant_user; EXECUTE stmt; DEALLOCATE PREPARE stmt;
END//
CREATE DEFINER='root'@'localhost' PROCEDURE myh_admin.drop_tenant_database(
  IN p_database VARCHAR(64), IN p_user VARCHAR(32)
) SQL SECURITY DEFINER
BEGIN
  IF p_database NOT REGEXP '^myh_[0-9]+_[a-z0-9_]{1,43}_[0-9a-f]{8}$'
     OR p_user NOT REGEXP '^u[0-9]+_[0-9a-f]{12}$' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Invalid generated tenant identifier';
  END IF;
  SET @drop_db = CONCAT('DROP DATABASE IF EXISTS `', p_database, '`');
  PREPARE stmt FROM @drop_db; EXECUTE stmt; DEALLOCATE PREPARE stmt;
  SET @drop_user = CONCAT('DROP USER IF EXISTS ', QUOTE(p_user), '@''172.23.%''');
  PREPARE stmt FROM @drop_user; EXECUTE stmt; DEALLOCATE PREPARE stmt;
END//
CREATE DEFINER='root'@'localhost' PROCEDURE myh_admin.reset_tenant_password(
  IN p_user VARCHAR(32), IN p_password VARCHAR(255)
) SQL SECURITY DEFINER
BEGIN
  IF p_user NOT REGEXP '^u[0-9]+_[0-9a-f]{12}$' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Invalid generated tenant identifier';
  END IF;
  SET @alter_user = CONCAT('ALTER USER ', QUOTE(p_user), '@''172.23.%'' IDENTIFIED BY ', QUOTE(p_password));
  PREPARE stmt FROM @alter_user; EXECUTE stmt; DEALLOCATE PREPARE stmt;
END//
DELIMITER ;
